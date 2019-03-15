
#!/usr/bin/env python3
import sys
import os
import numpy as np
sys.path.append(os.path.abspath(os.path.join('../../', 'src')))
import gym
import argparse
import torch
import torch.nn.functional as F
import torch.optim as optim
from tensorboardX import SummaryWriter
import actions
import agents
import runner
from wrapper import build_env_wrapper
import wrapper
import loss

from networks import actor_critic_net
from common import hyperparameters, logger, utils
from memory import ExperienceReplayBuffer


if __name__ == "__main__":
    # CONFIG
	params = hyperparameters.PARAMS['pong_a2c']
	parser = argparse.ArgumentParser()
	parser.add_argument("--cuda", default=False, action="store_true", help="Enable Cuda")
	args = parser.parse_args()
	device = torch.device("cuda" if args.cuda else "cpu")

	# INIT ENV
	env, observation_space, action_space = build_env_wrapper(params['env_name'], env_type=params['env_type'])

	# LOGGING
	writer = SummaryWriter(comment="-" + params['run_name'] + "-a2c")

	# NETWORK
	net = actor_critic_net.Network(observation_space, action_space).to(device)

	# AGENT
	agent = agents.PolicyGradientAgent(net, preprocessor=utils.float32_preprocessor, apply_softmax=True)

	# RUNNER
	exp_source = runner.RunnerSourceFirstLast(env, agent, gamma=params['gamma'], steps_count=params['step_count'])
	optimizer = optim.Adam(net.parameters(), lr=params['learning_rate'], eps=1e-3)

	batch = []

	with logger.RewardTracker(writer, stop_reward=18) as tracker:
		for step_idx, exp in enumerate(exp_source):
			batch.append(exp)

			# handle new rewards
			new_rewards = exp_source.pop_total_rewards()
			if new_rewards:
				if tracker.reward(new_rewards[0], step_idx):
					break

			if len(batch) < params['batch_size']:
				continue

			states_v, actions_t, vals_ref_v = unpack_batch(batch, net, device=device)
			batch.clear()

			optimizer.zero_grad()
			logits_v, value_v = net(states_v)
			loss_value_v = F.mse_loss(value_v.squeeze(-1), vals_ref_v)

			log_prob_v = F.log_softmax(logits_v, dim=1)
			adv_v = vals_ref_v - value_v.detach()
			log_prob_actions_v = adv_v * log_prob_v[range(BATCH_SIZE), actions_t]
			loss_policy_v = -log_prob_actions_v.mean()

			prob_v = F.softmax(logits_v, dim=1)
			entropy_loss_v = ENTROPY_BETA * (prob_v * log_prob_v).sum(dim=1).mean()

			# calculate policy gradients only
			loss_policy_v.backward(retain_graph=True)
			grads = np.concatenate([p.grad.data.cpu().numpy().flatten()
									for p in net.parameters()
									if p.grad is not None])

			# apply entropy and value gradients
			loss_v = entropy_loss_v + loss_value_v
			loss_v.backward()
			nn_utils.clip_grad_norm_(net.parameters(), CLIP_GRAD)
			optimizer.step()
			# get full loss
			loss_v += loss_policy_v
