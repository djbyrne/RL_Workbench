#!/usr/bin/env python3
import sys, os

sys.path.append(os.path.abspath(os.path.join("../../", "src")))

import gym
import argparse

import torch
import torch.optim as optim

from tensorboardX import SummaryWriter
from wrapper import build_env_wrapper
import config

import actions, agents, runner, common, wrapper, runner
from networks import dqn_noisy_net
from common import hyperparameters, logger
from memory import ExperienceReplayBuffer

if __name__ == "__main__":
    # CONFIG
    params = config.PARAMS["pong"]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cuda", default=False, action="store_true", help="Enable Cuda"
    )
    args = parser.parse_args()
    device = torch.device("cuda" if args.cuda else "cpu")

    # INIT ENV
    env, observation_space, action_space = build_env_wrapper(
        params["env_name"], env_type=params["env_type"]
    )

    # LOGGING
    writer = SummaryWriter(comment="-" + params["run_name"] + "-noisy")

    # NETWORK
    net = dqn_noisy_net.Network(env.observation_space.shape, env.action_space.n).to(
        device
    )
    tgt_net = agents.TargetNetwork(net)

    # AGENT
    selector = actions.ArgmaxActionSelector()
    agent = agents.DQNAgent(net, selector, device=device)

    # RUNNER
    exp_source = runner.RunnerSourceFirstLast(
        env, agent, gamma=params["gamma"]
    )  # increase the number of steps for the runner
    buffer = ExperienceReplayBuffer(exp_source, buffer_size=params["replay_size"])
    optimizer = optim.Adam(net.parameters(), lr=params["learning_rate"])

    frame_idx = 0

    # TRAIN
    with logger.RewardTracker(writer, params["stop_reward"]) as reward_tracker:
        while True:
            frame_idx += 1
            buffer.populate(1)

            new_rewards = exp_source.pop_total_rewards()
            if new_rewards:
                if reward_tracker.reward(new_rewards[0], frame_idx):
                    break

            if len(buffer) < params["replay_initial"]:
                continue

            # learning step
            optimizer.zero_grad()
            batch = buffer.sample(params["batch_size"])
            loss_v = loss.calc_loss_dqn(
                batch, net, tgt_net.target_model, params["gamma"], device
            )  # increase gamma by n-steps
            loss_v.backward()
            optimizer.step()

            if frame_idx % params["target_net_sync"] == 0:
                tgt_net.sync()

            # Log noise ratio as the agent trains
            if frame_idx % 500 == 0:
                for layer_idx, sigma_l2 in enumerate(net.noisy_layers_sigma_snr()):
                    writer.add_scalar(
                        "sigma_snr_layer_%d" % (layer_idx + 1), sigma_l2, frame_idx
                    )
