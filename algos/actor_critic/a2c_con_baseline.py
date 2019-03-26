#!/usr/bin/env python3
import os
import time
import math
import ptan
import gym
import argparse
from tensorboardX import SummaryWriter
import sys
import os
import numpy as np

sys.path.append(os.path.abspath(os.path.join("../../", "src")))
from networks import actor_critic_continuous
import actions
import agents
import runner
import ac_common
import config
from wrapper import build_multi_env, build_env_wrapper
from common import logger

# from lib import model, common

import numpy as np
import torch
import torch.optim as optim
import torch.nn.functional as F


ENV_ID = "Pendulum-v0"
GAMMA = 0.99
REWARD_STEPS = 2
BATCH_SIZE = 32
LEARNING_RATE = 5e-5
ENTROPY_BETA = 1e-4

TEST_ITERS = 10000


def test_net(net, env, count=10, device="cpu"):
    rewards = 0.0
    steps = 0
    for _ in range(count):
        obs = env.reset()
        while True:
            obs_v = ptan.agent.float32_preprocessor([obs]).to(device)
            mu_v = net(obs_v)[0]
            action = mu_v.squeeze(dim=0).data.cpu().numpy()
            action = np.clip(action, -1, 1)
            obs, reward, done, _ = env.step(action)
            rewards += reward
            steps += 1
            if done:
                break
    return rewards / count, steps / count


def calc_logprob(mu_v, var_v, actions_v):
    p1 = - ((mu_v - actions_v) ** 2) / (2*var_v.clamp(min=1e-3))
    p2 = - torch.log(torch.sqrt(2 * math.pi * var_v))
    return p1 + p2


if __name__ == "__main__":
    params = config.PARAMS["pendulum"]
    parser = argparse.ArgumentParser()
    parser.add_argument("--cuda", default=False, action='store_true', help='Enable CUDA')
    parser.add_argument("-n", "--name", required=False, help="Name of the run")
    args = parser.parse_args()
    device = "cpu"

    save_path = os.path.join("saves", "a2c-con")
    os.makedirs(save_path, exist_ok=True)

    # env = gym.make(ENV_ID)
    envs, observation_space, action_space = build_multi_env(
        params["env_name"], env_type=params["env_type"], num_envs=params["num_env"]
    )
    test_env = gym.make(ENV_ID)

    net = actor_critic_continuous.Network(observation_space, action_space).to(device)
    print(net)

    writer = SummaryWriter(comment="-a2c-con")
    agent = agents.ContinuousAgent(net, device=device)
    exp_source = runner.RunnerSourceFirstLast(envs, agent, GAMMA, steps_count=REWARD_STEPS)

    optimizer = optim.Adam(net.parameters(), lr=params["learning_rate"])

    batch = []
    best_reward = None
    with logger.RewardTracker(writer, stop_reward=params["stop_reward"]) as tracker:
        with ptan.common.utils.TBMeanTracker(writer, batch_size=10) as tb_tracker:
            for step_idx, exp in enumerate(exp_source):
                rewards_steps = exp_source.pop_rewards_steps()
                if rewards_steps:
                    rewards, steps = zip(*rewards_steps)
                    tb_tracker.track("episode_steps", steps[0], step_idx)
                    tracker.reward(rewards[0], step_idx)

                # if step_idx % TEST_ITERS == 0:
                #     ts = time.time()
                #     rewards, steps = test_net(net, test_env, device=device)
                #     print("Test done is %.2f sec, reward %.3f, steps %d" % (
                #         time.time() - ts, rewards, steps))
                #     writer.add_scalar("test_reward", rewards, step_idx)
                #     writer.add_scalar("test_steps", steps, step_idx)
                #     if best_reward is None or best_reward < rewards:
                #         if best_reward is not None:
                #             print("Best reward updated: %.3f -> %.3f" % (best_reward, rewards))
                #             name = "best_%+.3f_%d.dat" % (rewards, step_idx)
                #             fname = os.path.join(save_path, name)
                #             torch.save(net.state_dict(), fname)
                #         best_reward = rewards

                batch.append(exp)
                if len(batch) < BATCH_SIZE:
                    continue

                states_v, actions_v, vals_ref_v = \
                    ac_common.unpack_batch_continuous(batch, net, last_val_gamma=GAMMA ** REWARD_STEPS, device=device)
                batch.clear()

                optimizer.zero_grad()
                mu_v, var_v, value_v = net(states_v)

                loss_value_v = F.mse_loss(value_v.squeeze(-1), vals_ref_v)

                adv_v = vals_ref_v.unsqueeze(dim=-1) - value_v.detach()
                log_prob_v = adv_v * calc_logprob(mu_v, var_v, actions_v)
                loss_policy_v = -log_prob_v.mean()
                entropy_loss_v = ENTROPY_BETA * (-(torch.log(2*math.pi*var_v) + 1)/2).mean()

                loss_v = loss_policy_v + entropy_loss_v + loss_value_v
                loss_v.backward()
                optimizer.step()

                tb_tracker.track("advantage", adv_v, step_idx)
                tb_tracker.track("values", value_v, step_idx)
                tb_tracker.track("batch_rewards", vals_ref_v, step_idx)
                tb_tracker.track("loss_entropy", entropy_loss_v, step_idx)
                tb_tracker.track("loss_policy", loss_policy_v, step_idx)
                tb_tracker.track("loss_value", loss_value_v, step_idx)
                tb_tracker.track("loss_total", loss_v, step_idx)
