import os

import numpy as np
import numpy.random as rd
import torch
import torch.nn as nn

from AgentNet import QNet, QNetTwin, QNetDuel  # Q-learning based
from AgentNet import Actor, Critic, CriticTwin  # DDPG, TD3
from AgentNet import ActorSAC, CriticTwinShared  # SAC
from AgentNet import ActorPPO, CriticAdv  # PPO
from AgentNet import ActorGAE, CriticAdvTwin  # AdvGAE
from AgentNet import InterDPG, InterSPG, InterGAE  # share params between Actor and Critic

"""Zen4Jia1Hao2, GitHub: YonV1943 ElegantRL (Pytorch model-free DRL)
reference
TD3 https://github.com/sfujim/TD3 good++
TD3 https://github.com/nikhilbarhate99/TD3-PyTorch-BipedalWalker-v2 good
PPO https://github.com/zhangchuheng123/Reinforcement-Implementation/blob/master/code/ppo.py good+
PPO https://github.com/Jiankai-Sun/Proximal-Policy-Optimization-in-Pytorch/blob/master/ppo.py bad
PPO https://github.com/openai/baselines/tree/master/baselines/ppo2 normal-
SAC https://github.com/TianhongDai/reinforcement-learning-algorithms/tree/master/rl_algorithms/sac normal -
SQL https://github.com/gouxiangchen/soft-Q-learning/blob/master/sql.py bad-
DUEL https://github.com/gouxiangchen/dueling-DQN-pytorch good
"""


class AgentDDPG:  # DEMO (tutorial only, simplify, low effective)
    def __init__(self, state_dim, action_dim, net_dim):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        self.act = Actor(state_dim, action_dim, net_dim).to(self.device)
        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=2e-4)

        self.act_target = Actor(state_dim, action_dim, net_dim).to(self.device)
        self.act_target.load_state_dict(self.act.state_dict())

        self.cri = Critic(state_dim, action_dim, net_dim).to(self.device)
        self.cri_optimizer = torch.optim.Adam(self.cri.parameters(), lr=2e-4)

        self.cri_target = Critic(state_dim, action_dim, net_dim).to(self.device)
        self.cri_target.load_state_dict(self.cri.state_dict())

        self.criterion = nn.MSELoss()

        '''training record'''
        self.step = 0

        '''extension'''
        self.ou_noise = OrnsteinUhlenbeckProcess(size=action_dim, sigma=0.3)
        # I hate OU-Process in RL because of its too much hyper-parameters.

    def update_buffer(self, env, memo, max_step, max_action, reward_scale, gamma):
        reward_sum = 0.0
        step = 0

        state = env.reset()
        for step in range(max_step):
            '''inactive with environment'''
            action = self.select_actions((state,))[0] + self.ou_noise()
            # action = action.clip(-1, 1)
            next_state, reward, done, _ = env.step(action * max_action)

            reward_sum += reward

            '''update replay buffer'''
            reward_ = reward * reward_scale
            mask = 0.0 if done else gamma
            memo.add_memo((reward_, mask, state, action, next_state))

            state = next_state
            if done:
                break

        self.step = step  # update_parameters() need self.step
        return (reward_sum,), (step,)

    def update_parameters(self, buffer, _max_step, batch_size, _update_gap):
        loss_a_sum = 0.0
        loss_c_sum = 0.0

        # Here, the step_sum we interact in env is equal to the parameters update times
        update_times = self.step
        for _ in range(update_times):
            with torch.no_grad():
                rewards, masks, states, actions, next_states = buffer.random_sample(batch_size, self.device)

                next_action = self.act_target(next_states)
                next_q_target = self.cri_target(next_states, next_action)
                q_target = rewards + masks * next_q_target

            """critic loss"""
            q_eval = self.cri(states, actions)
            critic_loss = self.criterion(q_eval, q_target)
            loss_c_sum += critic_loss.item()

            self.cri_optimizer.zero_grad()
            critic_loss.backward()
            self.cri_optimizer.step()

            """actor loss"""
            action_cur = self.act(states)
            actor_loss = -self.cri(states, action_cur).mean()  # update parameters by sample policy gradient
            loss_a_sum += actor_loss.item()

            self.act_optimizer.zero_grad()
            actor_loss.backward()
            self.act_optimizer.step()

            soft_target_update(self.act_target, self.act)
            soft_target_update(self.cri_target, self.cri)

        loss_a_avg = loss_a_sum / update_times
        loss_c_avg = loss_c_sum / update_times
        return loss_a_avg, loss_c_avg

    def select_actions(self, states, explore_noise=0.0):  # CPU array to GPU tensor to CPU array
        states = torch.tensor(states, dtype=torch.float32, device=self.device)
        actions = self.act(states, explore_noise).cpu().data.numpy()
        return actions

    def save_or_load_model(self, mod_dir, if_save):  # 2020-05-20
        act_save_path = '{}/actor.pth'.format(mod_dir)
        cri_save_path = '{}/critic.pth'.format(mod_dir)

        if if_save:
            torch.save(self.act.state_dict(), act_save_path)
            torch.save(self.cri.state_dict(), cri_save_path)
            # print("Saved act and cri:", mod_dir)
        elif os.path.exists(act_save_path):
            act_dict = torch.load(act_save_path, map_location=lambda storage, loc: storage)
            self.act.load_state_dict(act_dict)
            self.act_target.load_state_dict(act_dict)
            cri_dict = torch.load(cri_save_path, map_location=lambda storage, loc: storage)
            self.cri.load_state_dict(cri_dict)
            self.cri_target.load_state_dict(cri_dict)
        else:
            print("FileNotFound when load_model: {}".format(mod_dir))


class AgentBasicAC:  # DEMO (formal, basic Actor-Critic Methods, it is a DDPG without OU-Process)
    def __init__(self, state_dim, action_dim, net_dim):
        use_dn = False  # soft target update is conflict with use_densenet
        self.learning_rate = 1e-4
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        actor_dim = net_dim
        self.act = Actor(state_dim, action_dim, actor_dim).to(self.device)
        self.act.train()
        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate)

        self.act_target = Actor(state_dim, action_dim, actor_dim).to(self.device)
        self.act_target.eval()
        self.act_target.load_state_dict(self.act.state_dict())

        critic_dim = int(net_dim * 1.25)
        self.cri = Critic(state_dim, action_dim, critic_dim).to(self.device)
        self.cri.train()
        self.cri_optimizer = torch.optim.Adam(self.cri.parameters(), lr=self.learning_rate)

        self.cri_target = Critic(state_dim, action_dim, critic_dim).to(self.device)
        self.cri_target.eval()
        self.cri_target.load_state_dict(self.cri.state_dict())

        self.criterion = nn.SmoothL1Loss()

        '''training record'''
        self.state = None  # env.reset()
        self.reward_sum = 0.0
        self.step = 0
        self.update_counter = 0  # delay update counter

        '''constant'''
        self.explore_noise = 0.05  # standard deviation of explore noise
        self.policy_noise = 0.1  # standard deviation of policy noise
        self.update_freq = 1  # set as 1 or 2 for soft target update

    def update_buffer(self, env, buffer, max_step, max_action, reward_scale, gamma):
        explore_noise = self.explore_noise  # standard deviation of explore noise
        self.act.eval()

        rewards = list()
        steps = list()
        for _ in range(max_step):
            '''inactive with environment'''
            action = self.select_actions((self.state,), explore_noise)[0]
            next_state, reward, done, _ = env.step(action * max_action)

            self.reward_sum += reward
            self.step += 1

            '''update replay buffer'''
            reward_ = reward * reward_scale
            mask = 0.0 if done else gamma
            buffer.add_memo((reward_, mask, self.state, action, next_state))

            self.state = next_state
            if done:
                rewards.append(self.reward_sum)
                self.reward_sum = 0.0

                steps.append(self.step)
                self.step = 0

                self.state = env.reset()
        return rewards, steps

    def update_parameters(self, buffer, max_step, batch_size, repeat_times):
        policy_noise = self.policy_noise  # standard deviation of policy noise
        update_freq = self.update_freq  # delay update frequency, for soft target update
        self.act.train()

        loss_a_sum = 0.0
        loss_c_sum = 0.0

        k = 1.0 + buffer.now_len / buffer.max_len
        batch_size_ = int(batch_size * k)
        update_times = int(max_step * k)

        for i in range(update_times * repeat_times):
            with torch.no_grad():
                reward, mask, state, action, next_state = buffer.random_sample(batch_size_, self.device)

                next_action = self.act_target(next_state, policy_noise)
                q_target = self.cri_target(next_state, next_action)
                q_target = reward + mask * q_target

            '''critic_loss'''
            q_eval = self.cri(state, action)
            critic_loss = self.criterion(q_eval, q_target)
            loss_c_sum += critic_loss.item()

            self.cri_optimizer.zero_grad()
            critic_loss.backward()
            self.cri_optimizer.step()

            '''actor_loss'''
            if i % repeat_times == 0:
                action_pg = self.act(state)  # policy gradient
                actor_loss = -self.cri(state, action_pg).mean()  # policy gradient
                loss_a_sum += actor_loss.item()

                self.act_optimizer.zero_grad()
                actor_loss.backward()
                self.act_optimizer.step()

            '''soft target update'''
            self.update_counter += 1
            if self.update_counter >= update_freq:
                self.update_counter = 0
                soft_target_update(self.act_target, self.act)  # soft target update
                soft_target_update(self.cri_target, self.cri)  # soft target update

        loss_a_avg = loss_a_sum / update_times
        loss_c_avg = loss_c_sum / (update_times * repeat_times)
        return loss_a_avg, loss_c_avg

    def select_actions(self, states, explore_noise=0.0):  # CPU array to GPU tensor to CPU array
        states = torch.tensor(states, dtype=torch.float32, device=self.device)
        actions = self.act(states, explore_noise)  # tensor
        return actions.cpu().data.numpy()  # array

    def save_or_load_model(self, cwd, if_save):  # 2020-07-07
        act_save_path = '{}/actor.pth'.format(cwd)
        cri_save_path = '{}/critic.pth'.format(cwd)
        has_act = 'act' in dir(self)
        has_cri = 'cri' in dir(self)

        def load_torch_file(network, save_path):
            network_dict = torch.load(save_path, map_location=lambda storage, loc: storage)
            network.load_state_dict(network_dict)

        if if_save:
            torch.save(self.act.state_dict(), act_save_path) if has_act else None
            torch.save(self.cri.state_dict(), cri_save_path) if has_cri else None
            # print("Saved act and cri:", mod_dir)
        elif os.path.exists(act_save_path):
            load_torch_file(self.act, act_save_path) if has_act else None
            load_torch_file(self.cri, cri_save_path) if has_cri else None
        else:
            print("FileNotFound when load_model: {}".format(cwd))


class AgentTD3(AgentBasicAC):
    def __init__(self, state_dim, action_dim, net_dim):
        super(AgentBasicAC, self).__init__()
        self.learning_rate = 2e-4
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        actor_dim = net_dim
        self.act = Actor(state_dim, action_dim, actor_dim).to(self.device)
        self.act.train()
        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate)

        self.act_target = Actor(state_dim, action_dim, actor_dim).to(self.device)
        self.act_target.eval()
        self.act_target.load_state_dict(self.act.state_dict())

        critic_dim = int(net_dim * 1.25)
        self.cri = CriticTwin(state_dim, action_dim, critic_dim).to(self.device)
        self.cri.train()
        self.cri_optimizer = torch.optim.Adam(self.cri.parameters(), lr=self.learning_rate)

        self.cri_target = CriticTwin(state_dim, action_dim, critic_dim).to(self.device)
        self.cri_target.eval()
        self.cri_target.load_state_dict(self.cri.state_dict())

        self.criterion = nn.MSELoss()

        '''training record'''
        self.state = None  # env.reset()
        self.reward_sum = 0.0
        self.step = 0
        self.update_counter = 0  # delay update counter

        '''constant'''
        self.explore_noise = 0.1  # standard deviation of explore noise
        self.policy_noise = 0.2  # standard deviation of policy noise
        self.update_freq = 2  # delay update frequency, for soft target update

    def update_parameters(self, buffer, max_step, batch_size, repeat_times):
        """Main Different between DDPG and TD3:
        1. twin critics
        2. policy noise
        """
        policy_noise = self.policy_noise  # standard deviation of policy noise
        update_freq = self.update_freq  # delay update frequency, for soft target update
        self.act.train()

        loss_a_sum = 0.0
        loss_c_sum = 0.0

        k = 1.0 + buffer.now_len / buffer.max_len
        batch_size_ = int(batch_size * k)
        update_times = int(max_step * k)

        for i in range(update_times):
            with torch.no_grad():
                reward, mask, state, action, next_s = buffer.random_sample(batch_size_, self.device)

                next_a = self.act_target(next_s, policy_noise)  # policy noise
                next_q_target = torch.min(*self.cri_target.get__q1_q2(next_s, next_a))  # twin critics
                q_target = reward + mask * next_q_target

            '''critic_loss'''
            q1, q2 = self.cri.get__q1_q2(state, action)  # TD3
            critic_loss = self.criterion(q1, q_target) + self.criterion(q2, q_target)
            loss_c_sum += critic_loss.item() * 0.5  # TD3

            self.cri_optimizer.zero_grad()
            critic_loss.backward()
            self.cri_optimizer.step()

            '''actor_loss'''
            action_pg = self.act(state)  # policy gradient
            # actor_loss = -self.cri(state, action_pg).mean()  # policy gradient
            actor_loss = -torch.min(*self.cri.get__q1_q2(state, action_pg)).mean()  # policy gradient
            loss_a_sum += actor_loss.item()

            self.act_optimizer.zero_grad()
            actor_loss.backward()
            self.act_optimizer.step()

            '''target update'''
            self.update_counter += 1
            if self.update_counter == update_freq:
                self.update_counter = 0
                soft_target_update(self.act_target, self.act)  # soft target update
                soft_target_update(self.cri_target, self.cri)  # soft target update

        loss_a_avg = loss_a_sum / update_times
        loss_c_avg = loss_c_sum / (update_times * repeat_times)
        return loss_a_avg, loss_c_avg


class AgentSAC(AgentBasicAC):
    def __init__(self, state_dim, action_dim, net_dim):
        super(AgentBasicAC, self).__init__()
        self.learning_rate = 1e-4
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        actor_dim = net_dim
        self.act = ActorSAC(state_dim, action_dim, actor_dim, use_dn=False).to(self.device)
        self.act.train()
        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate)
        # SAC uses target update network for critic only. Not for actor

        critic_dim = int(net_dim * 1.25)
        self.cri = CriticTwin(state_dim, action_dim, critic_dim).to(self.device)
        self.cri.train()
        self.cri_optimizer = torch.optim.Adam(self.cri.parameters(), lr=self.learning_rate)

        self.cri_target = CriticTwin(state_dim, action_dim, critic_dim).to(self.device)
        self.cri_target.eval()
        self.cri_target.load_state_dict(self.cri.state_dict())

        self.criterion = nn.MSELoss()

        '''training record'''
        self.state = None  # env.reset()
        self.reward_sum = 0.0
        self.step = 0
        self.update_counter = 0

        '''extension: auto-alpha for maximum entropy'''
        self.log_alpha = torch.zeros(1, requires_grad=True, device=self.device)
        self.alpha = self.log_alpha.exp()
        self.alpha_optimizer = torch.optim.Adam((self.log_alpha,), lr=self.learning_rate)
        self.target_entropy = np.log(action_dim) * 0.98

        '''constant'''
        self.explore_noise = True  # stochastic policy choose noise_std by itself.
        self.update_freq = 1  # delay update frequency, for soft target update

    def update_parameters(self, buffer, max_step, batch_size, repeat_times):
        loss_a_sum = 0.0
        loss_c_sum = 0.0

        k = 1.0 + buffer.now_len / buffer.max_len
        batch_size_ = int(batch_size * k)
        update_times = int(max_step * k)

        for i in range(update_times * repeat_times):
            with torch.no_grad():
                reward, mask, state, action, next_s = buffer.random_sample(batch_size_, self.device)

                next_a_noise, next_log_prob = self.act.get__a__log_prob(next_s)
                next_q_target = torch.min(*self.cri_target.get__q1_q2(next_s, next_a_noise))  # CriticTwin
                next_q_target = next_q_target + next_log_prob * self.alpha  # SAC, alpha
                q_target = reward + mask * next_q_target
            '''critic_loss'''
            q1_value, q2_value = self.cri.get__q1_q2(state, action)  # CriticTwin
            critic_loss = self.criterion(q1_value, q_target) + self.criterion(q2_value, q_target)
            loss_c_sum += critic_loss.item() * 0.5  # CriticTwin

            self.cri_optimizer.zero_grad()
            critic_loss.backward()
            self.cri_optimizer.step()

            '''actor_loss'''
            if i % repeat_times == 0:
                # stochastic policy
                actions_noise, log_prob = self.act.get__a__log_prob(state)  # policy gradient
                # auto alpha
                alpha_loss = (self.log_alpha * (log_prob - self.target_entropy).detach()).mean()
                self.alpha_optimizer.zero_grad()
                alpha_loss.backward()
                self.alpha_optimizer.step()

                # policy gradient
                self.alpha = self.log_alpha.exp()
                # q_eval_pg = self.cri(state, actions_noise)  # policy gradient
                q_eval_pg = torch.min(*self.cri.get__q1_q2(state, actions_noise))  # policy gradient, stable but slower
                actor_loss = -(q_eval_pg + log_prob * self.alpha).mean()  # policy gradient
                loss_a_sum += actor_loss.item()

                self.act_optimizer.zero_grad()
                actor_loss.backward()
                self.act_optimizer.step()

            """target update"""
            soft_target_update(self.cri_target, self.cri)  # soft target update

        loss_a_avg = loss_a_sum / update_times
        loss_c_avg = loss_c_sum / (update_times * repeat_times)
        return loss_a_avg, loss_c_avg


class AgentInterAC(AgentBasicAC):  # warning: sth. wrong
    def __init__(self, state_dim, action_dim, net_dim):
        super(AgentBasicAC, self).__init__()
        # use_dn = True  # SNAC, use_dn (DenseNet) and (Spectral Normalization)
        self.learning_rate = 1e-4
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        self.act = InterDPG(state_dim, action_dim, net_dim).to(self.device)
        self.act.train()
        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate)

        self.act_target = InterDPG(state_dim, action_dim, net_dim).to(self.device)
        self.act_target.eval()
        self.act_target.load_state_dict(self.act.state_dict())

        self.criterion = nn.SmoothL1Loss()

        '''training record'''
        self.state = None  # env.reset()
        self.reward_sum = 0.0
        self.step = 0
        self.update_counter = 0

        '''extension: auto learning rate of actor'''
        self.trust_rho = TrustRho()

        '''constant'''
        self.explore_noise = 0.2  # standard deviation of explore noise
        self.policy_noise = 0.4  # standard deviation of policy noise
        self.update_freq = 2 ** 7  # delay update frequency, for hard target update

    def update_parameters(self, buffer, max_step, batch_size, repeat_times):
        policy_noise = self.policy_noise
        update_freq = self.update_freq
        self.act.eval()

        loss_a_sum = 0.0
        loss_c_sum = 0.0

        k = 1.0 + buffer.now_len / buffer.max_len
        batch_size_ = int(batch_size * k)
        update_times = int(max_step * k)

        for i in range(update_times * repeat_times):
            with torch.no_grad():
                reward, mask, state, action, next_state = buffer.random_sample(batch_size_, self.device)

                next_q_target, next_action = self.act_target.next__q_a(
                    state, next_state, policy_noise)
                q_target = reward + mask * next_q_target

            '''critic loss'''
            q_eval = self.act.critic(state, action)
            critic_loss = self.criterion(q_eval, q_target)
            loss_c_tmp = critic_loss.item()
            loss_c_sum += loss_c_tmp
            rho = self.trust_rho.update_rho(loss_c_tmp)

            '''actor correction term'''
            actor_term = self.criterion(self.act(next_state), next_action)

            if i % repeat_times == 0:
                '''actor loss'''
                action_cur = self.act(state)  # policy gradient
                actor_loss = -self.act_target.critic(state, action_cur).mean()  # policy gradient
                # NOTICE! It is very important to use act_target.critic here instead act.critic
                # Or you can use act.critic.deepcopy(). Whatever you cannot use act.critic directly.
                loss_a_sum += actor_loss.item()

                united_loss = critic_loss + actor_term * (1 - rho) + actor_loss * (rho * 0.5)
            else:
                united_loss = critic_loss + actor_term * (1 - rho)

            """united loss"""
            self.act_optimizer.zero_grad()
            united_loss.backward()
            self.act_optimizer.step()

            self.update_counter += 1
            if self.update_counter == update_freq:
                self.update_counter = 0
                if rho > 0.1:
                    self.act_target.load_state_dict(self.act.state_dict())

        loss_a_avg = loss_a_sum / update_times
        loss_c_avg = loss_c_sum / (update_times * repeat_times)
        return loss_a_avg, loss_c_avg


class AgentDeepSAC(AgentBasicAC):
    def __init__(self, state_dim, action_dim, net_dim):
        super(AgentBasicAC, self).__init__()
        use_dn = True  # and use hard target update
        self.learning_rate = 1e-4
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        actor_dim = net_dim
        self.act = ActorSAC(state_dim, action_dim, actor_dim, use_dn).to(self.device)
        self.act.train()
        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate)

        self.act_target = ActorSAC(state_dim, action_dim, net_dim, use_dn).to(self.device)
        self.act_target.eval()
        self.act_target.load_state_dict(self.act.state_dict())

        critic_dim = int(net_dim * 1.25)
        self.cri = CriticTwinShared(state_dim, action_dim, critic_dim, use_dn).to(self.device)
        self.cri.train()
        self.cri_optimizer = torch.optim.Adam(self.cri.parameters(), lr=self.learning_rate)

        self.cri_target = CriticTwinShared(state_dim, action_dim, critic_dim, use_dn).to(self.device)
        self.cri_target.eval()
        self.cri_target.load_state_dict(self.cri.state_dict())

        self.criterion = nn.SmoothL1Loss()

        '''training record'''
        self.state = None  # env.reset()
        self.reward_sum = 0.0
        self.step = 0
        self.update_counter = 0

        '''extension: auto-alpha for maximum entropy'''
        self.target_entropy = np.log(action_dim + 1) * 0.5
        self.log_alpha = torch.tensor((-self.target_entropy * np.e,), requires_grad=True, device=self.device)
        self.alpha_optimizer = torch.optim.Adam((self.log_alpha,), lr=self.learning_rate)
        '''extension: auto learning rate of actor'''
        self.trust_rho = TrustRho()

        '''constant'''
        self.explore_noise = True  # stochastic policy choose noise_std by itself.

    def update_parameters(self, buffer, max_step, batch_size, repeat_times):
        self.act.train()

        loss_a_sum = 0.0
        loss_c_sum = 0.0

        alpha = self.log_alpha.exp().detach()

        k = 1.0 + buffer.now_len / buffer.max_len
        batch_size_ = int(batch_size * k)
        update_times_c = int(max_step * k)

        update_times_a = 0
        for i in range(1, update_times_c):
            with torch.no_grad():
                reward, mask, state, action, next_s = buffer.random_sample(batch_size_, self.device)

                next_a_noise, next_log_prob = self.act_target.get__a__log_prob(next_s)
                next_q_target = torch.min(*self.cri_target.get__q1_q2(next_s, next_a_noise))  # CriticTwin
                q_target = reward + mask * (next_q_target + next_log_prob * alpha)  # policy entropy
            '''critic_loss'''
            q1_value, q2_value = self.cri.get__q1_q2(state, action)  # CriticTwin
            critic_loss = self.criterion(q1_value, q_target) + self.criterion(q2_value, q_target)
            loss_c_tmp = critic_loss.item() * 0.5  # CriticTwin
            loss_c_sum += loss_c_tmp
            rho = self.trust_rho.update_rho(loss_c_tmp)

            self.cri_optimizer.zero_grad()
            critic_loss.backward()
            self.cri_optimizer.step()

            actions_noise, log_prob = self.act.get__a__log_prob(state)  # policy gradient

            '''auto temperature parameter (alpha)'''
            alpha_loss = (self.log_alpha * (log_prob - self.target_entropy).detach()).mean()
            self.alpha_optimizer.zero_grad()
            alpha_loss.backward()
            self.alpha_optimizer.step()
            with torch.no_grad():
                self.log_alpha[:] = self.log_alpha.clamp(-16, 1)  # todo fix bug
            alpha = self.log_alpha.exp().detach()

            '''actor_loss'''
            if update_times_a / i < rho + 0.5:
                update_times_a += 1

                q_eval_pg = torch.min(*self.cri.get__q1_q2(state, actions_noise))  # policy gradient, stable but slower

                actor_loss = -(q_eval_pg + log_prob * alpha).mean()  # policy gradient
                loss_a_sum += actor_loss.item()

                self.act_optimizer.param_groups[0]['lr'] = self.learning_rate * rho
                self.act_optimizer.zero_grad()
                actor_loss.backward()
                self.act_optimizer.step()

                """target update"""
                soft_target_update(self.act_target, self.act)  # soft target update
            """target update"""
            soft_target_update(self.cri_target, self.cri)  # soft target update

        loss_a_avg = (loss_a_sum / update_times_a) if update_times_a else 0.0
        loss_c_avg = loss_c_sum / update_times_c
        return loss_a_avg, loss_c_avg


class AgentInterSAC(AgentBasicAC):  # Integrated Soft Actor-Critic Methods
    def __init__(self, state_dim, action_dim, net_dim):
        super(AgentBasicAC, self).__init__()
        self.learning_rate = 1e-4
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        actor_dim = net_dim
        self.act = InterSPG(state_dim, action_dim, actor_dim).to(self.device)
        self.act.train()

        self.cri = self.act

        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate, betas=(0.5, 0.999))

        self.act_target = InterSPG(state_dim, action_dim, net_dim).to(self.device)
        self.act_target.eval()
        self.act_target.load_state_dict(self.act.state_dict())

        self.criterion = nn.SmoothL1Loss()

        '''training record'''
        self.state = None  # env.reset()
        self.reward_sum = 0.0
        self.step = 0
        self.update_counter = 0

        '''extension: auto-alpha for maximum entropy'''
        self.target_entropy = np.log(action_dim + 1) * 0.5  # todo
        self.log_alpha = torch.tensor((-self.target_entropy * np.e,), requires_grad=True, device=self.device)
        self.alpha_optimizer = torch.optim.Adam((self.log_alpha,), lr=self.learning_rate, betas=(0.5, 0.999))
        print('log_alpha:', self.log_alpha.item())  # todo init log_alpha

        '''extension: auto learning rate of actor'''
        self.trust_rho = TrustRho()

        '''constant'''
        self.explore_noise = True  # stochastic policy choose noise_std by itself.

    def update_parameters(self, buffer, max_step, batch_size, repeat_times):
        self.act.train()

        loss_a_list = list()
        loss_c_list = list()

        alpha = self.log_alpha.exp().detach()
        k = 1.0 + buffer.now_len / buffer.max_len
        batch_size_ = int(batch_size * k)
        update_times = int(max_step * k)

        log_prob = None  # todo print

        for i in range(update_times):
            with torch.no_grad():
                reward, mask, state, action, next_s = buffer.random_sample(batch_size_, self.device)

                next_a_noise, next_log_prob = self.act_target.get__a__log_prob(next_s)
                next_q_target = torch.min(*self.act_target.get__q1_q2(next_s, next_a_noise))  # twin critic
                q_target = reward + mask * (next_q_target + next_log_prob * alpha)  # policy entropy
            '''critic_loss'''
            q1_value, q2_value = self.cri.get__q1_q2(state, action)  # CriticTwin
            critic_loss = self.criterion(q1_value, q_target) + self.criterion(q2_value, q_target)
            loss_c_tmp = critic_loss.item() * 0.5  # CriticTwin
            loss_c_list.append(loss_c_tmp)
            rho = self.trust_rho.update_rho(loss_c_tmp)

            '''stochastic policy'''
            a1_mean, a1_log_std, a_noise, log_prob = self.act.get__a__avg_std_noise_prob(state)  # policy gradient

            '''action correction term'''
            a2_mean, a2_log_std = self.act_target.get__a__std(state)
            actor_term = self.criterion(a1_mean, a2_mean) + self.criterion(a1_log_std, a2_log_std)

            '''auto alpha'''
            alpha_loss = (rho * self.log_alpha * (log_prob - self.target_entropy).detach()).mean()
            self.alpha_optimizer.zero_grad()
            alpha_loss.backward()
            self.alpha_optimizer.step()
            with torch.no_grad():
                self.log_alpha[:] = self.log_alpha.clamp(-16, 1)  # todo fix bug
            alpha = self.log_alpha.exp().detach()

            '''actor_loss'''
            if rho > 2 ** -8:  # (self.rho>2**-8) ~= (self.critic_loss<2.355)
                q_eval_pg = torch.min(*self.act_target.get__q1_q2(state, a_noise))  # policy gradient
                actor_loss = -(q_eval_pg + log_prob * alpha).mean()  # policy gradient
                loss_a_list.append(actor_loss.item())
            else:
                actor_loss = 0

            united_loss = critic_loss + actor_term * (1 - rho) + actor_loss * rho
            self.act_optimizer.zero_grad()
            united_loss.backward()
            self.act_optimizer.step()

            soft_target_update(self.act_target, self.act, tau=2 ** -8)

        print(np.array(
            (alpha.item(), self.log_alpha.item(), log_prob.mean().item(), self.target_entropy)
        ).round(3))  # todo show alpha
        loss_a_avg = (sum(loss_a_list) / len(loss_a_list)) if len(loss_a_list) > 0 else 0.0
        loss_c_avg = sum(loss_c_list) / len(loss_c_list)
        return loss_a_avg, loss_c_avg


class AgentPPO:
    def __init__(self, state_dim, action_dim, net_dim):
        self.learning_rate = 1e-4  # learning rate of actor
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        self.act = ActorPPO(state_dim, action_dim, net_dim).to(self.device)
        self.act.train()
        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate, )  # betas=(0.5, 0.99))

        self.cri = CriticAdv(state_dim, net_dim).to(self.device)
        self.cri.train()
        self.cri_optimizer = torch.optim.Adam(self.cri.parameters(), lr=self.learning_rate, )  # betas=(0.5, 0.99))

        self.criterion = nn.SmoothL1Loss()

    def update_buffer(self, env, buffer, max_step, max_action, reward_scale, gamma):
        # collect tuple (reward, mask, state, action, log_prob, )
        buffer.storage_list = list()  # PPO is an online policy RL algorithm.
        # If I comment the above code, it becomes a offline policy PPO.
        # Using Offline in PPO (or GAE) won't speed up training but slower

        rewards = list()
        steps = list()

        step_counter = 0
        while step_counter < buffer.max_memo:
            state = env.reset()

            reward_sum = 0
            step_sum = 0

            for step_sum in range(max_step):
                actions, log_probs = self.select_actions((state,), explore_noise=True)
                action = actions[0]
                log_prob = log_probs[0]

                next_state, reward, done, _ = env.step(np.tanh(action) * max_action)
                reward_sum += reward

                mask = 0.0 if done else gamma

                reward_ = reward * reward_scale
                buffer.push(reward_, mask, state, action, log_prob, )

                if done:
                    break

                state = next_state

            rewards.append(reward_sum)
            steps.append(step_sum)

            step_counter += step_sum
        return rewards, steps

    def update_parameters(self, buffer, _max_step, batch_size, repeat_times):
        self.act.train()
        self.cri.train()
        clip = 0.25  # ratio.clamp(1 - clip, 1 + clip)
        lambda_adv = 0.98  # why 0.98? cannot seem to use 0.99
        lambda_entropy = 0.01  # could be 0.02
        # repeat_times = 8 could be 2**3 ~ 2**5

        loss_a_sum = 0.0  # just for print
        loss_c_sum = 0.0  # just for print

        '''the batch for training'''
        max_memo = len(buffer)
        all_batch = buffer.sample()
        all_reward, all_mask, all_state, all_action, all_log_prob = [
            torch.tensor(ary, dtype=torch.float32, device=self.device)
            for ary in (all_batch.reward, all_batch.mask, all_batch.state, all_batch.action, all_batch.log_prob,)
        ]
        # with torch.no_grad():
        all__new_v = self.cri(all_state).detach_()  # all new value

        '''compute old_v (old policy value), adv_v (advantage value) 
        refer: GAE. ICLR 2016. Generalization Advantage Estimate. 
        https://arxiv.org/pdf/1506.02438.pdf'''
        all__delta = torch.empty(max_memo, dtype=torch.float32, device=self.device)
        all__old_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # old policy value
        all__adv_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # advantage value

        prev_old_v = 0  # old q value
        prev_new_v = 0  # new q value
        prev_adv_v = 0  # advantage q value
        for i in range(max_memo - 1, -1, -1):
            all__delta[i] = all_reward[i] + all_mask[i] * prev_new_v - all__new_v[i]
            all__old_v[i] = all_reward[i] + all_mask[i] * prev_old_v
            all__adv_v[i] = all__delta[i] + all_mask[i] * prev_adv_v * lambda_adv

            prev_old_v = all__old_v[i]
            prev_new_v = all__new_v[i]
            prev_adv_v = all__adv_v[i]

        all__adv_v = (all__adv_v - all__adv_v.mean()) / (all__adv_v.std() + 1e-6)  # advantage_norm:

        '''mini batch sample'''
        sample_times = int(repeat_times * max_memo / batch_size)
        for _ in range(sample_times):
            '''random sample'''
            # indices = rd.choice(max_memo, batch_size, replace=True)  # False)
            indices = rd.randint(max_memo, size=batch_size)

            state = all_state[indices]
            action = all_action[indices]
            advantage = all__adv_v[indices]
            old_value = all__old_v[indices].unsqueeze(1)
            old_log_prob = all_log_prob[indices]

            """Adaptive KL Penalty Coefficient
            loss_KLPEN = surrogate_obj + value_obj * lambda_value + entropy_obj * lambda_entropy
            loss_KLPEN = (value_obj * lambda_value) + (surrogate_obj + entropy_obj * lambda_entropy)
            loss_KLPEN = (critic_loss) + (actor_loss)
            """

            '''critic_loss'''
            new_log_prob = self.act.compute__log_prob(state, action)
            new_value = self.cri(state)

            critic_loss = self.criterion(new_value, old_value) / (old_value.std() + 1e-6)
            loss_c_sum += critic_loss.item()  # just for print
            self.cri_optimizer.zero_grad()
            critic_loss.backward()
            self.cri_optimizer.step()

            '''actor_loss'''
            # surrogate objective of TRPO
            ratio = torch.exp(new_log_prob - old_log_prob)
            surrogate_obj0 = advantage * ratio
            surrogate_obj1 = advantage * ratio.clamp(1 - clip, 1 + clip)
            surrogate_obj = -torch.mean(torch.min(surrogate_obj0, surrogate_obj1))
            # policy entropy
            loss_entropy = torch.mean(torch.exp(new_log_prob) * new_log_prob)

            actor_loss = surrogate_obj + loss_entropy * lambda_entropy
            loss_a_sum += actor_loss.item()  # just for print
            self.act_optimizer.zero_grad()
            actor_loss.backward()
            self.act_optimizer.step()

        self.act.eval()
        self.cri.eval()
        loss_a_avg = loss_a_sum / sample_times
        loss_c_avg = loss_c_sum / sample_times
        return loss_a_avg, loss_c_avg

    def select_actions(self, states, explore_noise=0.0):  # CPU array to GPU tensor to CPU array
        states = torch.tensor(states, dtype=torch.float32, device=self.device)

        if explore_noise == 0.0:
            a_mean = self.act(states)
            a_mean = a_mean.cpu().data.numpy()
            return a_mean.tanh()  # todo with tanh() fix bug
        else:
            a_noise, log_prob = self.act.get__a__log_prob(states)
            a_noise = a_noise.cpu().data.numpy()
            log_prob = log_prob.cpu().data.numpy()
            return a_noise, log_prob  # not tanh()

    def save_or_load_model(self, cwd, if_save):  # 2020-05-20
        act_save_path = '{}/actor.pth'.format(cwd)
        cri_save_path = '{}/critic.pth'.format(cwd)
        has_cri = 'cri' in dir(self)

        def load_torch_file(network, save_path):
            network_dict = torch.load(save_path, map_location=lambda storage, loc: storage)
            network.load_state_dict(network_dict)

        if if_save:
            torch.save(self.act.state_dict(), act_save_path)
            torch.save(self.cri.state_dict(), cri_save_path) if has_cri else None
            # print("Saved act and cri:", mod_dir)
        elif os.path.exists(act_save_path):
            load_torch_file(self.act, act_save_path)
            load_torch_file(self.cri, cri_save_path) if has_cri else None
        else:
            print("FileNotFound when load_model: {}".format(cwd))


class AgentGAE(AgentPPO):
    def __init__(self, state_dim, action_dim, net_dim):
        super(AgentPPO, self).__init__()
        self.learning_rate = 1e-4  # learning rate of actor
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        self.act = ActorGAE(state_dim, action_dim, net_dim).to(self.device)
        self.act.train()
        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate, )  # betas=(0.5, 0.99))

        self.cri = CriticAdvTwin(state_dim, net_dim).to(self.device)
        self.cri.train()
        self.cri_optimizer = torch.optim.Adam(self.cri.parameters(), lr=self.learning_rate, )  # betas=(0.5, 0.99))
        # cannot use actor target network
        # not need to use critic target network

        self.criterion = nn.SmoothL1Loss()

    def update_parameters(self, buffer, _max_step, batch_size, repeat_times):
        """Differences between AgentGAE and AgentPPO are:
        1. In AgentGAE, critic use TwinCritic. In AgentPPO, critic use a single critic.
        2. In AgentGAE, log_std is output by actor. In AgentPPO, log_std is just a trainable tensor.
        """

        self.act.train()
        self.cri.train()
        clip = 0.25  # ratio.clamp(1 - clip, 1 + clip)
        lambda_adv = 0.98  # why 0.98? cannot seem to use 0.99
        lambda_entropy = 0.01  # could be 0.02
        # repeat_times = 8 could be 2**2 ~ 2**4

        loss_a_sum = 0.0  # just for print
        loss_c_sum = 0.0  # just for print

        '''the batch for training'''
        max_memo = len(buffer)
        all_batch = buffer.sample()
        all_reward, all_mask, all_state, all_action, all_log_prob = [
            torch.tensor(ary, dtype=torch.float32, device=self.device)
            for ary in (all_batch.reward, all_batch.mask, all_batch.state, all_batch.action, all_batch.log_prob,)
        ]
        # with torch.no_grad():
        # all__new_v = self.cri(all_state).detach_()  # all new value
        # all__new_v = torch.min(*self.cri(all_state)).detach_()  # TwinCritic
        with torch.no_grad():
            b_size = 256
            all__new_v = torch.cat([torch.min(*self.cri(all_state[i:i + b_size]))
                                    for i in range(0, all_state.size()[0], b_size)], dim=0)

        '''compute old_v (old policy value), adv_v (advantage value) 
        refer: Generalization Advantage Estimate. ICLR 2016. 
        https://arxiv.org/pdf/1506.02438.pdf
        '''
        all__delta = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # delta of q value
        all__old_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # old policy value
        all__adv_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # advantage value

        prev_old_v = 0  # old q value
        prev_new_v = 0  # new q value
        prev_adv_v = 0  # advantage q value
        for i in range(max_memo - 1, -1, -1):
            all__delta[i] = all_reward[i] + all_mask[i] * prev_new_v - all__new_v[i]
            all__old_v[i] = all_reward[i] + all_mask[i] * prev_old_v
            all__adv_v[i] = all__delta[i] + all_mask[i] * prev_adv_v * lambda_adv

            prev_old_v = all__old_v[i]
            prev_new_v = all__new_v[i]
            prev_adv_v = all__adv_v[i]

        all__adv_v = (all__adv_v - all__adv_v.mean()) / (all__adv_v.std() + 1e-6)  # advantage_norm:

        '''mini batch sample'''
        sample_times = int(repeat_times * max_memo / batch_size)
        for _ in range(sample_times):
            '''random sample'''
            # indices = rd.choice(max_memo, batch_size, replace=True)  # False)
            indices = rd.randint(max_memo, size=batch_size)

            state = all_state[indices]
            action = all_action[indices]
            advantage = all__adv_v[indices]
            old_value = all__old_v[indices].unsqueeze(1)
            old_log_prob = all_log_prob[indices]

            """Adaptive KL Penalty Coefficient
            loss_KLPEN = surrogate_obj + value_obj * lambda_value + entropy_obj * lambda_entropy
            loss_KLPEN = (value_obj * lambda_value) + (surrogate_obj + entropy_obj * lambda_entropy)
            loss_KLPEN = (critic_loss) + (actor_loss)
            """

            '''critic_loss'''
            new_log_prob = self.act.compute__log_prob(state, action)
            new_value1, new_value2 = self.cri(state)  # TwinCritic
            # new_log_prob, new_value1, new_value2 = self.act_target.compute__log_prob(state, action)

            critic_loss = (self.criterion(new_value1, old_value) +
                           self.criterion(new_value2, old_value)) / (old_value.std() * 2 + 1e-6)
            loss_c_sum += critic_loss.item() * 0.5  # just for print
            self.cri_optimizer.zero_grad()
            critic_loss.backward()
            self.cri_optimizer.step()

            '''actor_loss'''
            # PPO's surrogate objective of TRPO
            ratio = (new_log_prob - old_log_prob).exp()
            surrogate_obj0 = advantage * ratio
            surrogate_obj1 = advantage * ratio.clamp(1 - clip, 1 + clip)
            surrogate_obj = -torch.min(surrogate_obj0, surrogate_obj1).mean()
            # policy entropy
            loss_entropy = (new_log_prob.exp() * new_log_prob).mean()

            actor_loss = surrogate_obj + loss_entropy * lambda_entropy
            loss_a_sum += actor_loss.item()  # just for print
            self.act_optimizer.zero_grad()
            actor_loss.backward()
            self.act_optimizer.step()

        loss_a_avg = loss_a_sum / sample_times
        loss_c_avg = loss_c_sum / sample_times
        return loss_a_avg, loss_c_avg


class AgentInterGAE(AgentPPO):
    def __init__(self, state_dim, action_dim, net_dim):
        super(AgentPPO, self).__init__()
        self.learning_rate = 1e-4
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        self.act = InterGAE(state_dim, action_dim, net_dim).to(self.device)
        self.act.train()
        self.cri = self.act.get__q1_q2
        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate, )  # betas=(0.5, 0.99))

        self.act_target = InterGAE(state_dim, action_dim, net_dim).to(self.device)
        self.act_target.eval()
        self.act_target.load_state_dict(self.act.state_dict())
        self.cri_target = self.act_target.get__q1_q2

        self.criterion = nn.SmoothL1Loss()

    def update_parameters(self, buffer, _max_step, batch_size, repeat_times):
        self.act.train()
        # self.cri.train()
        clip = 0.25  # ratio.clamp(1 - clip, 1 + clip)
        lambda_adv = 0.98  # why 0.98? cannot seem to use 0.99
        lambda_entropy = 0.01  # could be 0.02
        # repeat_times = 8 could be 2**2 ~ 2**4

        loss_a_sum = 0.0  # just for print
        loss_c_sum = 0.0  # just for print

        '''the batch for training'''
        max_memo = len(buffer)
        all_batch = buffer.sample()
        all_reward, all_mask, all_state, all_action, all_log_prob = [
            torch.tensor(ary, dtype=torch.float32, device=self.device)
            for ary in (all_batch.reward, all_batch.mask, all_batch.state, all_batch.action, all_batch.log_prob,)
        ]
        # with torch.no_grad():
        # all__new_v = self.cri(all_state).detach_()  # all new value
        # all__new_v = torch.min(*self.cri(all_state)).detach_()  # TwinCritic
        with torch.no_grad():
            b_size = 128
            all__new_v = torch.cat([torch.min(*self.cri_target(all_state[i:i + b_size]))
                                    for i in range(0, all_state.size()[0], b_size)], dim=0)

        '''compute old_v (old policy value), adv_v (advantage value) 
        refer: Generalization Advantage Estimate. ICLR 2016. 
        https://arxiv.org/pdf/1506.02438.pdf
        '''
        all__delta = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # delta of q value
        all__old_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # old policy value
        all__adv_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # advantage value

        prev_old_v = 0  # old q value
        prev_new_v = 0  # new q value
        prev_adv_v = 0  # advantage q value
        for i in range(max_memo - 1, -1, -1):
            all__delta[i] = all_reward[i] + all_mask[i] * prev_new_v - all__new_v[i]
            all__old_v[i] = all_reward[i] + all_mask[i] * prev_old_v
            all__adv_v[i] = all__delta[i] + all_mask[i] * prev_adv_v * lambda_adv

            prev_old_v = all__old_v[i]
            prev_new_v = all__new_v[i]
            prev_adv_v = all__adv_v[i]

        all__adv_v = (all__adv_v - all__adv_v.mean()) / (all__adv_v.std() + 1e-6)  # advantage_norm:

        '''mini batch sample'''
        sample_times = int(repeat_times * max_memo / batch_size)
        for _ in range(sample_times):
            '''random sample'''
            # indices = rd.choice(max_memo, batch_size, replace=True)  # False)
            indices = rd.randint(max_memo, size=batch_size)

            state = all_state[indices]
            action = all_action[indices]
            advantage = all__adv_v[indices]
            old_value = all__old_v[indices].unsqueeze(1)
            old_log_prob = all_log_prob[indices]

            """Adaptive KL Penalty Coefficient
            loss_KLPEN = surrogate_obj + value_obj * lambda_value + entropy_obj * lambda_entropy
            loss_KLPEN = (value_obj * lambda_value) + (surrogate_obj + entropy_obj * lambda_entropy)
            loss_KLPEN = (critic_loss) + (actor_loss)
            """

            '''critic_loss'''
            new_log_prob = self.act.compute__log_prob(state, action)
            new_value1, new_value2 = self.cri(state)  # TwinCritic
            # new_log_prob, new_value1, new_value2 = self.act_target.compute__log_prob(state, action)

            critic_loss = (self.criterion(new_value1, old_value) +
                           self.criterion(new_value2, old_value)) / (old_value.std() * 2 + 1e-6)
            loss_c_sum += critic_loss.item()  # just for print
            # self.cri_optimizer.zero_grad()
            # critic_loss.backward()
            # self.cri_optimizer.step()

            '''actor_loss'''
            # surrogate objective of TRPO
            ratio = (new_log_prob - old_log_prob).exp()
            surrogate_obj0 = advantage * ratio
            surrogate_obj1 = advantage * ratio.clamp(1 - clip, 1 + clip)
            surrogate_obj = -torch.min(surrogate_obj0, surrogate_obj1).mean()
            # policy entropy
            loss_entropy = (new_log_prob.exp() * new_log_prob).mean()

            actor_loss = surrogate_obj + loss_entropy * lambda_entropy
            loss_a_sum += actor_loss.item()  # just for print

            united_loss = actor_loss + critic_loss
            self.act_optimizer.zero_grad()
            united_loss.backward()
            self.act_optimizer.step()

            soft_target_update(self.act_target, self.act, tau=2 ** -8)

        loss_a_avg = loss_a_sum / sample_times
        loss_c_avg = loss_c_sum / sample_times
        return loss_a_avg, loss_c_avg


class AgentDiscreteGAE(AgentGAE):  # wait to be elegant
    def __init__(self, state_dim, action_dim, net_dim):
        AgentGAE.__init__(self, state_dim, action_dim, net_dim)

        self.cri_target = CriticAdvTwin(state_dim, net_dim).to(self.device)
        self.cri_target.eval()
        self.cri_target.load_state_dict(self.cri.state_dict())

        '''extension: DiscreteGAE'''
        self.softmax = nn.Softmax(dim=1)
        self.action_dim = action_dim

    def update_buffer(self, env, buffer, max_step, _max_action, reward_scale, gamma):
        """Difference between AgentDiscreteGAE and AgentPPO. In AgentDiscreteGAE, we have:
        1. Actor output a vector as the probability of discrete action.
        2. We save action vector into replay buffer instead of action int.
        """
        # collect tuple (reward, mask, state, action, log_prob, )
        buffer.storage_list = list()  # PPO is an online policy RL algorithm.
        # If I comment the above code, it becomes a offline policy PPO.
        # Using Offline in PPO (or GAE) won't speed up training but slower

        rewards = list()
        steps = list()

        step_counter = 0
        while step_counter < buffer.max_memo:
            state = env.reset()

            reward_sum = 0
            step_sum = 0

            for step_sum in range(max_step):
                a_ints, actions, log_probs = self.select_actions((state,), explore_noise=True)  # for DiscreteGAE
                a_int = a_ints[0]  # for discrete action
                action = actions[0]
                log_prob = log_probs[0]

                next_state, reward, done, _ = env.step(a_int)  # discrete action
                reward_sum += reward

                mask = 0.0 if done else gamma

                reward_ = reward * reward_scale
                buffer.push(reward_, mask, state, action, log_prob, )

                if done:
                    break

                state = next_state

            rewards.append(reward_sum)
            steps.append(step_sum)

            step_counter += step_sum
        return rewards, steps

    def update_parameters_online(self, buffer, batch_size, repeat_times):
        """Differences between AgentDiscreteGAE and AgentGAE are:
        try target network on critic
        """

        self.act.train()
        self.cri.train()
        clip = 0.25  # ratio.clamp(1 - clip, 1 + clip)
        lambda_adv = 0.98  # why 0.98? cannot seem to use 0.99
        lambda_entropy = 0.01  # could be 0.02
        # repeat_times = 8 could be 2**2 ~ 2**4

        loss_a_sum = 0.0  # just for print
        loss_c_sum = 0.0  # just for print

        '''the batch for training'''
        max_memo = len(buffer)
        all_batch = buffer.sample()
        all_reward, all_mask, all_state, all_action, all_log_prob = [
            torch.tensor(ary, dtype=torch.float32, device=self.device)
            for ary in (all_batch.reward, all_batch.mask, all_batch.state, all_batch.action, all_batch.log_prob,)
        ]
        # with torch.no_grad():
        # all__new_v = self.cri(all_state).detach_()  # all new value
        all__new_v = torch.min(*self.cri_target(all_state)).detach_()  # TwinCritic

        '''compute old_v (old policy value), adv_v (advantage value) 
        refer: Generalization Advantage Estimate. ICLR 2016. 
        https://arxiv.org/pdf/1506.02438.pdf
        '''
        all__delta = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # delta of q value
        all__old_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # old policy value
        all__adv_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # advantage value

        prev_old_v = 0  # old q value
        prev_new_v = 0  # new q value
        prev_adv_v = 0  # advantage q value
        for i in range(max_memo - 1, -1, -1):
            all__delta[i] = all_reward[i] + all_mask[i] * prev_new_v - all__new_v[i]
            all__old_v[i] = all_reward[i] + all_mask[i] * prev_old_v
            all__adv_v[i] = all__delta[i] + all_mask[i] * prev_adv_v * lambda_adv

            prev_old_v = all__old_v[i]
            prev_new_v = all__new_v[i]
            prev_adv_v = all__adv_v[i]

        all__adv_v = (all__adv_v - all__adv_v.mean()) / (all__adv_v.std() + 1e-6)  # advantage_norm:

        '''mini batch sample'''
        sample_times = int(repeat_times * max_memo / batch_size)
        for _ in range(sample_times):
            '''random sample'''
            # indices = rd.choice(max_memo, batch_size, replace=True)  # False)
            indices = rd.randint(max_memo, size=batch_size)

            state = all_state[indices]
            action = all_action[indices]
            advantage = all__adv_v[indices]
            old_value = all__old_v[indices].unsqueeze(1)
            old_log_prob = all_log_prob[indices]

            """Adaptive KL Penalty Coefficient
            loss_KLPEN = surrogate_obj + value_obj * lambda_value + entropy_obj * lambda_entropy
            loss_KLPEN = (value_obj * lambda_value) + (surrogate_obj + entropy_obj * lambda_entropy)
            loss_KLPEN = (critic_loss) + (actor_loss)
            """

            '''critic_loss'''
            new_log_prob = self.act.compute__log_prob(state, action)
            new_value1, new_value2 = self.cri(state)  # TwinCritic
            # new_log_prob, new_value1, new_value2 = self.act_target.compute__log_prob(state, action)

            critic_loss = (self.criterion(new_value1, old_value) +
                           self.criterion(new_value2, old_value)) / (old_value.std() * 2 + 1e-6)
            loss_c_sum += critic_loss.item() * 0.5  # just for print
            self.cri_optimizer.zero_grad()
            critic_loss.backward()
            self.cri_optimizer.step()

            '''actor_loss'''
            # surrogate objective of TRPO
            ratio = (new_log_prob - old_log_prob).exp()
            surrogate_obj0 = advantage * ratio
            surrogate_obj1 = advantage * ratio.clamp(1 - clip, 1 + clip)
            surrogate_obj = -torch.min(surrogate_obj0, surrogate_obj1).mean()
            # policy entropy
            loss_entropy = (new_log_prob.exp() * new_log_prob).mean()

            actor_loss = surrogate_obj + loss_entropy * lambda_entropy
            loss_a_sum += actor_loss.item()  # just for print
            self.act_optimizer.zero_grad()
            actor_loss.backward()
            self.act_optimizer.step()

            # soft_target_update(self.cri_target, self.cri)  # soft update
        self.cri_target.load_state_dict(self.cri.state_dict())  # hard update is obviously better than soft update

        loss_a_avg = loss_a_sum / sample_times
        loss_c_avg = loss_c_sum / sample_times
        return loss_a_avg, loss_c_avg

    def select_actions(self, states, explore_noise=0.0):  # CPU array to GPU tensor to CPU array
        """In DiscreteGAE,
        1. a_int = a_mean.argmax(dim=1) for evaluating
        2. we return (a_int, a_noise, log_prob) for training,
           a_int for env.step (select according action_probability_vector)
           a_noise for replay buffer.
        """
        states = torch.tensor(states, dtype=torch.float32, device=self.device)

        if explore_noise == 0.0:
            a_mean = self.act(states)
            # a_mean = a_mean.cpu().data.numpy()
            # return a_mean
            a_int = a_mean.argmax(dim=1)
            return a_int.cpu().data.numpy()

        else:
            a_noise, log_prob = self.act.get__a__log_prob(states)
            a_prob = self.softmax(a_noise).cpu().data.numpy()

            a_noise = a_noise.cpu().data.numpy()
            log_prob = log_prob.cpu().data.numpy()

            a_int = [rd.choice(self.action_dim, p=prob)
                     for prob in a_prob]
            return a_int, a_noise, log_prob


class AgentDQN:  # 2020-06-06
    def __init__(self, state_dim, action_dim, net_dim):  # 2020-04-30
        self.learning_rate = 1e-4
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        actor_dim = net_dim
        self.act = QNet(state_dim, action_dim, actor_dim).to(self.device)
        self.act.train()
        self.act_optim = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate)

        self.criterion = nn.MSELoss()

        '''training record'''
        self.state = None  # env.reset()
        self.r_sum = 0.0  # the sum of rewards of an episode
        self.steps = 0
        self.action_dim = action_dim  # for update_buffer() epsilon-greedy

    def update_buffer(self, env, buffer, max_step, max_action, reward_scale, gamma):
        explore_rate = 0.1  # explore rate when update_buffer()
        self.act.eval()

        rewards = list()
        steps = list()
        for _ in range(max_step):
            '''inactive with environment'''
            if rd.rand() < explore_rate:  # explored policy for DQN: epsilon-Greedy
                action = rd.randint(self.action_dim)
            else:
                action = self.select_actions((self.state,), )[0]
            next_state, reward, done, _ = env.step(action * max_action)

            self.r_sum += reward
            self.steps += 1

            '''update replay buffer'''
            reward_ = reward * reward_scale
            mask = 0.0 if done else gamma
            buffer.add_memo((reward_, mask, self.state, action, next_state))

            self.state = next_state
            if done:
                rewards.append(self.r_sum)
                self.r_sum = 0.0

                steps.append(self.steps)
                self.steps = 0

                self.state = env.reset()
        return rewards, steps

    def update_parameters(self, buffer, max_step, batch_size, repeat_times):
        # in general, repeat_times == 1, and it is not necessary
        loss_c_sum = 0.0

        update_times = int(max_step * repeat_times)
        for _ in range(update_times):
            with torch.no_grad():
                rewards, masks, states, actions, next_states = buffer.random_sample(batch_size, self.device)

                next_q_target = self.act(next_states).max(dim=1, keepdim=True)[0]
                q_target = rewards + masks * next_q_target

            self.act.train()
            actions = actions.type(torch.long)
            q_eval = self.act(states).gather(1, actions)
            critic_loss = self.criterion(q_eval, q_target)
            loss_c_sum += critic_loss.item()

            self.act_optim.zero_grad()
            critic_loss.backward()
            self.act_optim.step()

        loss_a_avg = 0.0
        loss_c_avg = loss_c_sum / update_times
        return loss_a_avg, loss_c_avg

    def select_actions(self, states):  # state -> ndarray shape: (1, state_dim)
        states = torch.tensor(states, dtype=torch.float32, device=self.device)
        actions = self.act(states).argmax(dim=1).cpu().data.numpy()  # discrete action space
        return actions

    def save_or_load_model(self, mod_dir, if_save):
        act_save_path = '{}/actor.pth'.format(mod_dir)

        if if_save:
            torch.save(self.act.state_dict(), act_save_path)
            # print("Saved neural network:", mod_dir)
        elif os.path.exists(act_save_path):
            act_dict = torch.load(act_save_path, map_location=lambda storage, loc: storage)
            self.act.load_state_dict(act_dict)
        else:
            print("FileNotFound when load_model: {}".format(mod_dir))


class AgentDoubleDQN(AgentBasicAC):  # 2020-06-06 # I'm not sure.
    def __init__(self, state_dim, action_dim, net_dim):  # 2020-04-30
        super(AgentBasicAC, self).__init__()
        self.learning_rate = 1e-4
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        act = QNetTwin(state_dim, action_dim, net_dim).to(self.device)
        act.train()
        self.act = act
        self.act_optimizer = torch.optim.Adam(act.parameters(), lr=self.learning_rate)

        act_target = QNetTwin(state_dim, action_dim, net_dim).to(self.device)
        act_target.eval()
        self.act_target = act_target
        self.act_target.load_state_dict(act.state_dict())

        self.criterion = nn.SmoothL1Loss()
        self.softmax = nn.Softmax(dim=1)
        self.action_dim = action_dim

        '''training record'''
        self.state = None  # env.reset()
        self.reward_sum = 0.0
        self.step = 0
        self.update_counter = 0

        '''extension: rho and loss_c'''
        self.explore_rate = 0.25  # explore rate when update_buffer()
        self.explore_noise = True  # standard deviation of explore noise
        self.update_freq = 2 ** 7  # delay update frequency, for hard target update

    def update_parameters(self, buffer, max_step, batch_size, repeat_times):
        update_freq = self.update_freq  # delay update frequency, for soft target update
        self.act.train()

        # loss_a_sum = 0.0
        loss_c_sum = 0.0

        k = 1.0 + buffer.now_len / buffer.max_len
        batch_size_ = int(batch_size * k)
        update_times = int(max_step * k)

        for _ in range(update_times):
            with torch.no_grad():
                rewards, masks, states, actions, next_states = buffer.random_sample(batch_size_, self.device)

                q_target_next = self.act_target(next_states).max(dim=1, keepdim=True)[0]
                q_target = rewards + masks * q_target_next

            self.act.train()
            actions = actions.type(torch.long)
            q_eval1, q_eval2 = [qs.gather(1, actions) for qs in self.act.get__q1_q2(states)]
            critic_loss = self.criterion(q_eval1, q_target) + self.criterion(q_eval2, q_target)
            loss_c_tmp = critic_loss.item() * 0.5
            loss_c_sum += loss_c_tmp
            # self.trust_rho.append_loss_c(loss_c_tmp)

            self.act_optimizer.zero_grad()
            critic_loss.backward()
            self.act_optimizer.step()

            self.update_counter += 1
            if self.update_counter == update_freq:
                self.update_counter = 0
                # soft_target_update(self.act_target, self.act)
                self.act_target.load_state_dict(self.act.state_dict())  # hard target update

                # trust_rho = self.trust_rho.get_trust_rho()
                # self.act_optimizer.param_groups[0]['lr'] = self.learning_rate * trust_rho

        loss_a_avg = 0.0
        loss_c_avg = loss_c_sum / update_times
        return loss_a_avg, loss_c_avg

    def select_actions(self, states, explore_noise=0.0):  # 2020-07-07
        states = torch.tensor(states, dtype=torch.float32, device=self.device)  # state.size == (1, state_dim)
        actions = self.act(states, 0)

        # discrete action space
        if explore_noise == 0.0:
            a_ints = actions.argmax(dim=1).cpu().data.numpy()

        else:
            a_prob = self.softmax(actions).cpu().data.numpy()
            a_ints = [rd.choice(self.action_dim, p=prob)
                      for prob in a_prob]
        return a_ints


class AgentDuelingDQN(AgentBasicAC):  # 2020-07-07
    def __init__(self, state_dim, action_dim, net_dim):
        super(AgentBasicAC, self).__init__()
        self.learning_rate = 1e-4
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        self.act = QNetDuel(state_dim, action_dim, net_dim).to(self.device)
        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate)
        self.act.train()

        self.act_target = QNetDuel(state_dim, action_dim, net_dim).to(self.device)
        self.act_target.load_state_dict(self.act.state_dict())
        self.act_target.eval()

        self.criterion = nn.SmoothL1Loss()
        self.softmax = nn.Softmax(dim=1)
        self.action_dim = action_dim

        '''training record'''
        self.state = None  # env.reset()
        self.reward_sum = 0.0
        self.step = 0
        self.update_counter = 0

        '''extension: rho and loss_c'''
        self.explore_noise = True  # standard deviation of explore noise

    def update_buffer(self, env, buffer, max_step, max_action, reward_scale, gamma):
        explore_rate = 0.25  # todo hyper-parameters
        explore_noise = self.explore_noise  # standard deviation of explore noise
        self.act.eval()

        rewards = list()
        steps = list()
        for _ in range(max_step):
            '''inactive with environment'''
            explore_noise_ = explore_noise if rd.rand() < explore_rate else 0
            action = self.select_actions((self.state,), explore_noise_)[0]
            next_state, reward, done, _ = env.step(action * max_action)

            self.reward_sum += reward
            self.step += 1

            '''update replay buffer'''
            reward_ = reward * reward_scale
            mask = 0.0 if done else gamma
            buffer.add_memo((reward_, mask, self.state, action, next_state))

            self.state = next_state
            if done:
                rewards.append(self.reward_sum)
                self.reward_sum = 0.0

                steps.append(self.step)
                self.step = 0

                self.state = env.reset()
        return rewards, steps

    def update_parameters(self, buffer, max_step, batch_size, repeat_times):
        self.act.train()

        # loss_a_sum = 0.0
        loss_c_sum = 0.0

        k = 1.0 + buffer.now_len / buffer.max_len
        batch_size_ = int(batch_size * k)
        update_times = int(max_step * k)

        for _ in range(update_times):
            with torch.no_grad():
                rewards, masks, states, actions, next_states = buffer.random_sample(batch_size_, self.device)

                q_target_next = self.act_target(next_states).max(dim=1, keepdim=True)[0]
                q_target = rewards + masks * q_target_next

            self.act.train()
            a_ints = actions.type(torch.long)
            q_eval = self.act(states).gather(1, a_ints)
            critic_loss = self.criterion(q_eval, q_target)
            loss_c_tmp = critic_loss.item()
            loss_c_sum += loss_c_tmp

            self.act_optimizer.zero_grad()
            critic_loss.backward()
            self.act_optimizer.step()

            soft_target_update(self.act_target, self.act)

        loss_a_avg = 0.0
        loss_c_avg = loss_c_sum / update_times
        return loss_a_avg, loss_c_avg

    def select_actions(self, states, explore_noise=0.0):  # 2020-07-07
        states = torch.tensor(states, dtype=torch.float32, device=self.device)  # state.size == (1, state_dim)
        actions = self.act(states, 0)

        # discrete action space
        if explore_noise == 0.0:
            a_ints = actions.argmax(dim=1).cpu().data.numpy()
        else:
            a_prob = self.softmax(actions).cpu().data.numpy()
            a_ints = [rd.choice(self.action_dim, p=prob)
                      for prob in a_prob]
            # a_ints = rd.randint(self.action_dim, size=)
        return a_ints


class AgentEBM(AgentBasicAC):  # Energy Based Model (Soft Q-learning) I'm not sure. # plan
    # def __init__(self, state_dim, action_dim, net_dim):
    #     super(AgentBasicAC, self).__init__()
    pass


"""utils"""


def initial_exploration(env, memo, max_step, action_max, reward_scale, gamma, action_dim):
    state = env.reset()

    rewards = list()
    reward_sum = 0.0
    steps = list()
    step = 0

    if isinstance(action_max, int) and action_max == int(1):
        def random_uniform_policy_for_discrete_action():
            return rd.randint(action_dim)

        get_random_action = random_uniform_policy_for_discrete_action
        action_max = int(1)
    else:
        def random_uniform_policy_for_continuous_action():
            return rd.uniform(-1, 1, size=action_dim)

        get_random_action = random_uniform_policy_for_continuous_action

    global_step = 0
    while global_step < max_step:
        # action = np.tanh(rd.normal(0, 0.25, size=action_dim))  # zero-mean gauss exploration
        action = get_random_action()
        next_state, reward, done, _ = env.step(action * action_max)
        reward_sum += reward
        step += 1

        adjust_reward = reward * reward_scale
        mask = 0.0 if done else gamma
        memo.add_memo((adjust_reward, mask, state, action, next_state))

        state = next_state
        if done:
            rewards.append(reward_sum)
            steps.append(step)
            global_step += step

            state = env.reset()  # reset the environment
            reward_sum = 0.0
            step = 1

    memo.init_before_sample()
    return rewards, steps


def soft_target_update(target, online, tau=5e-3):
    for target_param, param in zip(target.parameters(), online.parameters()):
        target_param.data.copy_(tau * param.data + (1.0 - tau) * target_param.data)


class TrustRho:
    def __init__(self):
        self.loss_c_list = list()
        self.rho = 0.5  # could be range (0.0, np.e)
        self.update_counter = 0
        self.update_freq = 2 ** 7

    def update_rho(self, loss_c):
        # assert isinstance(loss_c, float)
        self.loss_c_list.append(loss_c)

        self.update_counter += 1
        if self.update_counter >= self.update_freq:
            self.update_counter = 0
            loss_c_avg = np.average(self.loss_c_list)
            self.loss_c_list = list()

            rho = np.exp(-loss_c_avg ** 2)
            self.rho = (self.rho + rho) * 0.5  # soft update
        return self.rho


class OrnsteinUhlenbeckProcess:  # I hate OU Process because there are too much hyper-parameters.
    def __init__(self, size, theta=0.15, sigma=0.3, x0=0.0, dt=1e-2):
        """
        Source: https://github.com/slowbull/DDPG/blob/master/src/explorationnoise.py
        I think that:
        It makes Zero-mean Gaussian Noise more stable.
        It helps agent explore better in a inertial system.
        """
        self.theta = theta
        self.sigma = sigma
        self.x0 = x0
        self.dt = dt
        self.size = size

    def __call__(self):
        noise = self.sigma * np.sqrt(self.dt) * rd.normal(size=self.size)
        x = self.x0 - self.theta * self.x0 * self.dt + noise
        self.x0 = x  # update x0
        return x


'''experiment replay buffer'''


class BufferList:
    def __init__(self, memo_max_len):
        self.memories = list()

        self.max_len = memo_max_len
        self.now_len = len(self.memories)

    def add_memo(self, memory_tuple):
        self.memories.append(memory_tuple)

    def init_before_sample(self):
        del_len = len(self.memories) - self.max_len
        if del_len > 0:
            del self.memories[:del_len]
            # print('Length of Deleted Memories:', del_len)

        self.now_len = len(self.memories)

    def random_sample(self, batch_size, device):
        # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # indices = rd.choice(self.memo_len, batch_size, replace=False)  # why perform worse?
        # indices = rd.choice(self.memo_len, batch_size, replace=True)  # why perform better?
        # same as:
        indices = rd.randint(self.now_len, size=batch_size)

        '''convert list into array'''
        arrays = [list()
                  for _ in range(5)]  # len(self.memories[0]) == 5
        for index in indices:
            items = self.memories[index]
            for item, array in zip(items, arrays):
                array.append(item)

        '''convert array into torch.tensor'''
        tensors = [torch.tensor(np.array(ary), dtype=torch.float32, device=device)
                   for ary in arrays]
        return tensors


class BufferArray:  # 2020-05-20
    def __init__(self, memo_max_len, state_dim, action_dim, ):
        state_dim = state_dim if isinstance(state_dim, int) else np.prod(state_dim)  # pixel-level state

        memo_dim = 1 + 1 + state_dim + action_dim + state_dim
        self.memories = np.empty((memo_max_len, memo_dim), dtype=np.float32)

        self.next_idx = 0
        self.is_full = False
        self.max_len = memo_max_len
        self.now_len = self.max_len if self.is_full else self.next_idx

        self.state_idx = 1 + 1 + state_dim  # reward_dim==1, done_dim==1
        self.action_idx = self.state_idx + action_dim

    def add_memo(self, memo_tuple):
        # memo_array == (reward, mask, state, action, next_state)
        self.memories[self.next_idx] = np.hstack(memo_tuple)
        self.next_idx = self.next_idx + 1
        if self.next_idx >= self.max_len:
            self.is_full = True
            self.next_idx = 0

    def extend_memo(self, memo_array):  # 2020-07-07
        # assert isinstance(memo_array, np.ndarray)
        size = memo_array.shape[0]
        next_idx = self.next_idx + size
        if next_idx < self.max_len:
            self.memories[self.next_idx:next_idx] = memo_array
        if next_idx >= self.max_len:
            if next_idx > self.max_len:
                self.memories[self.next_idx:self.max_len] = memo_array[:self.max_len - self.next_idx]
            self.is_full = True
            next_idx = next_idx - self.max_len
            self.memories[0:next_idx] = memo_array[-next_idx:]
        else:
            self.memories[self.next_idx:next_idx] = memo_array
        self.next_idx = next_idx

    def init_before_sample(self):
        self.now_len = self.max_len if self.is_full else self.next_idx

    def random_sample(self, batch_size, device):
        # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # indices = rd.choice(self.memo_len, batch_size, replace=False)  # why perform worse?
        # indices = rd.choice(self.memo_len, batch_size, replace=True)  # why perform better?
        # same as:
        indices = rd.randint(self.now_len, size=batch_size)
        memory = self.memories[indices]
        if device:
            memory = torch.tensor(memory, device=device)

        '''convert array into torch.tensor'''
        tensors = (
            memory[:, 0:1],  # rewards
            memory[:, 1:2],  # masks, mark == (1-float(done)) * gamma
            memory[:, 2:self.state_idx],  # states
            memory[:, self.state_idx:self.action_idx],  # actions
            memory[:, self.action_idx:],  # next_states
        )
        return tensors


class BufferArrayGPU:  # 2020-07-07, for mp__update_params()
    def __init__(self, memo_max_len, state_dim, action_dim, ):
        state_dim = state_dim if isinstance(state_dim, int) else np.prod(state_dim)  # pixel-level state

        memo_dim = 1 + 1 + state_dim + action_dim + state_dim
        assert torch.cuda.is_available()
        self.device = torch.device("cuda")
        self.memories = torch.empty((memo_max_len, memo_dim), dtype=torch.float32, device=self.device)

        self.next_idx = 0
        self.is_full = False
        self.max_len = memo_max_len
        self.now_len = self.max_len if self.is_full else self.next_idx

        self.state_idx = 1 + 1 + state_dim  # reward_dim==1, done_dim==1
        self.action_idx = self.state_idx + action_dim

    def add_memo(self, memo_tuple):
        """memo_tuple == (reward, mask, state, action, next_state)
        """
        memo_array = np.hstack(memo_tuple)
        self.memories[self.next_idx] = torch.tensor(memo_array, device=self.device)
        self.next_idx = self.next_idx + 1
        if self.next_idx >= self.max_len:
            self.is_full = True
            self.next_idx = 0

    def extend_memo(self, memo_array):  # 2020-07-07
        # assert isinstance(memo_array, np.ndarray)
        size = memo_array.shape[0]
        memo_tensor = torch.tensor(memo_array, device=self.device)

        next_idx = self.next_idx + size
        if next_idx < self.max_len:
            self.memories[self.next_idx:next_idx] = memo_tensor
        if next_idx >= self.max_len:
            if next_idx > self.max_len:
                self.memories[self.next_idx:self.max_len] = memo_tensor[:self.max_len - self.next_idx]
            self.is_full = True
            next_idx = next_idx - self.max_len
            self.memories[0:next_idx] = memo_tensor[-next_idx:]
        else:
            self.memories[self.next_idx:next_idx] = memo_tensor
        self.next_idx = next_idx

    def init_before_sample(self):
        self.now_len = self.max_len if self.is_full else self.next_idx

    def random_sample(self, batch_size, _device):  # _device should remove
        # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # indices = rd.choice(self.memo_len, batch_size, replace=False)  # why perform worse?
        # indices = rd.choice(self.memo_len, batch_size, replace=True)  # why perform better?
        # same as:
        indices = rd.randint(self.now_len, size=batch_size)
        memory = self.memories[indices]
        # if device:
        #     memory = torch.tensor(memory, device=device)

        '''convert array into torch.tensor'''
        tensors = (
            memory[:, 0:1],  # rewards
            memory[:, 1:2],  # masks, mark == (1-float(done)) * gamma
            memory[:, 2:self.state_idx],  # states
            memory[:, self.state_idx:self.action_idx],  # actions
            memory[:, self.action_idx:],  # next_states
        )
        return tensors


class BufferTuple:
    def __init__(self, memo_max_len):
        self.memories = list()

        self.max_len = memo_max_len
        self.now_len = None  # init in init_after_add_memo()

        from collections import namedtuple
        self.transition = namedtuple(
            'Transition', ('reward', 'mask', 'state', 'action', 'next_state',)
        )

    def add_memo(self, args):
        self.memories.append(self.transition(*args))

    def init_before_sample(self):
        del_len = len(self.memories) - self.max_len
        if del_len > 0:
            del self.memories[:del_len]
            # print('Length of Deleted Memories:', del_len)

        self.now_len = len(self.memories)

    def random_sample(self, batch_size, device):
        # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # indices = rd.choice(self.memo_len, batch_size, replace=False)  # why perform worse?
        # indices = rd.choice(self.memo_len, batch_size, replace=True)  # why perform better?
        # same as:
        indices = rd.randint(self.now_len, size=batch_size)

        '''convert tuple into array'''
        arrays = self.transition(*zip(*[self.memories[i] for i in indices]))

        '''convert array into torch.tensor'''
        tensors = [torch.tensor(np.array(ary), dtype=torch.float32, device=device)
                   for ary in arrays]
        return tensors


class BufferTupleOnline:
    def __init__(self, max_memo):
        self.max_memo = max_memo
        self.storage_list = list()
        from collections import namedtuple
        self.transition = namedtuple(
            'Transition',
            # ('state', 'value', 'action', 'log_prob', 'mask', 'next_state', 'reward')
            ('reward', 'mask', 'state', 'action', 'log_prob')
        )

    def push(self, *args):
        self.storage_list.append(self.transition(*args))

    def extend_memo(self, storage_list):
        self.storage_list.extend(storage_list)

    def sample(self):
        return self.transition(*zip(*self.storage_list))

    def __len__(self):
        return len(self.storage_list)

    def init_before_sample(self):
        pass  # compatibility
