from desktop_app.goal import Goal
from web_app.app_data import TopLevelData, GoalV2, Recurrence, GoalState
from web_app.users import User
from web_app.data_interface import DataInterface
from desktop_app.client import ClientV2

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def Goal_to_GoalV2(goal: Goal) -> GoalV2:
    def get_goal_state() -> GoalState:
        if goal.state:
            return GoalState.COMPLETED
        if goal.backlogged:
            return GoalState.BACKLOGGED
        return GoalState.ACTIVE

    def get_recurrence() -> Optional[Recurrence]:
        if not goal.repeat:
            return None

        return Recurrence(
            start=goal.metadata.creation_date,
            end=goal.repeat_end_date,
            repeat_period=timedelta(days=goal.repeat),
            paused=goal.paused
        )

    return GoalV2(
        id=goal.id,
        name=goal.name,
        state=get_goal_state(),
        description=goal.description,
        creation_date=goal.metadata.creation_date,
        completion_date=goal.metadata.completion_date,
        planned_completion_date=None,
        parent=goal.parent if goal.parent != -1 else None,
        children=goal.children,
        recurrence=get_recurrence()
    )

def convert_goals():
    client = ClientV2(True)
    goals = client.fetch_goals()

    data_interface = DataInterface(True)
    tld = TopLevelData(goals={goal.id: Goal_to_GoalV2(goal) for goal in goals}, edited=datetime.now())
    data_interface.save_data(tld, User("test"))
    with open(Path.home()/".todoist"/"data.json", "w") as file:
        file.write(tld.model_dump_json(indent=4))