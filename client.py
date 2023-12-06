import requests
import json

from typing import *
from goal import Goal

# this is a temporary hack
from server import Backend
backend = Backend()

class Client:
	URL = 'https://bavdvowkx7.execute-api.ap-southeast-2.amazonaws.com/prod/todoist2'
	HEADERS = {'x-api-key': 'jM6bHWXdes1Ih1XecNkD519IJr60dcAK2hL7k30O'}

	def __init__(self) -> None:
		pass

	def fetch_goals(self) -> List[Goal]:
		response = requests.get(self.URL, json={'request': 'fetch_goals'}, headers=self.HEADERS, verify=True)
		if response.status_code != 200:
			raise RuntimeError(f"Error in fetch_goals: {response.text}")
		self.goals = [Goal.from_dict(goal) for goal in json.loads(response.text)['goals']]
		return self.goals
	
	def add_goal(self, goal: Goal) -> None:
		self.goals.append(goal)
		request = {
			'request': 'add_goal',
			'goal': goal.to_dict()
		}
		response = requests.get(self.URL, json=request, headers=self.HEADERS, verify=True)
		if response.status_code != 200:
			raise RuntimeError(f"Error in add_goals: {response.text}")

	def delete_goal(self, idx: int) -> None:
		self.goals.pop(idx)
		request = {'request': 'delete_goal', 'goal_id': idx}
		response = requests.get(self.URL, json=request, headers=self.HEADERS, verify=True)
		if response.status_code != 200:
			raise RuntimeError(f"Error in delete_goal: {response.text}")
		
	def toggle_goal_state(self, idx: int) -> None:
		request = {'request': 'toggle_goal_state', 'goal_id': idx}
		response = requests.get(self.URL, json=request, headers=self.HEADERS, verify=True)
		if response.status_code != 200:
			raise RuntimeError(f"Error in toggle_goal_state: {response.text}")
		
	def backup_goals(self) -> None:
		response = requests.get(self.URL, json={'request': 'backup_goals'}, headers=self.HEADERS, verify=True)
		if response.status_code != 200:
			raise RuntimeError(f"Error in backup_goals: {response.text}")