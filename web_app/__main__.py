import os
import random
import click

from typing import *
from pathlib import Path
from flask import Flask, render_template, send_from_directory
from flask_bootstrap import Bootstrap5
from datetime import datetime

from desktop_app.client import ClientV2
from desktop_app.goal import Goal


app = Flask(__name__)

bootstrap = Bootstrap5(app)
client = ClientV2(debug=True)
def get_random_image() -> Path:
    BASE_DIR = Path(os.environ["RANDOM_IMAGES_DIR"])
    images = list(BASE_DIR.glob("*.jpeg"))

    return random.choice(images).absolute()

def get_summary_goals() -> List[Tuple[str, List[Goal]]]:
    now = datetime.now()
    def should_render(goal: Goal) -> bool:
        # reserve rendering of children to later
        if goal.parent != -1:
            return False
        if goal.backlogged:
            return False
        if goal.repeat:
            return False
        # hides goals that have been completed for a while
        days_since_completion = 0 if not goal.state else (now - goal.metadata.completion_date).days
        if days_since_completion > 2:
            return False
        
        return True
    
    goals = client.fetch_goals()
    goals = [goal for goal in goals if should_render(goal)]
    goals.sort(key=lambda goal: goal.metadata.creation_date.timestamp())

    goal_blocks = []
    last_date_label = Goal.NULL_DATE.date()
    for goal in goals:
        goal_date = goal.metadata.creation_date.date()
        if last_date_label != goal_date:
            last_date_label = goal_date
            goal_blocks.append((last_date_label.strftime("%d/%m/%Y"), [goal]))
        else:
            goal_blocks[-1] = (goal_blocks[-1][0], goal_blocks[-1][1] + [goal])

    return goal_blocks
    
@app.route('/')
def index():
    dated_goal_blocks = get_summary_goals()
    dated_goal_blocks.reverse()
    return render_template('index.html', dated_goal_blocks=dated_goal_blocks)

@app.route('/resources/<path:filename>')
def static_file(filename):
    image = get_random_image()
    print(f"Sending {image} for {filename}")
    return send_from_directory(directory=image.parent, path=image.name)

@click.command()
@click.option('--debug', is_flag=True)
def main(debug: bool):
    app.run(host='0.0.0.0', port=80, debug=debug)

if __name__ == '__main__':
    main()