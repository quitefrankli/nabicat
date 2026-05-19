import flask
import flask_login

from typing import * # type: ignore
from flask import request, Blueprint, render_template
from datetime import datetime

from web_app.helpers import limiter, from_req, cur_user
from web_app.todoist.data_interface import DataInterface, GoalState, Goal


goals_api = Blueprint('goals_api', __name__, url_prefix='/goal')

@goals_api.before_request
@flask_login.login_required
def require_login():
    pass

def get_default_redirect():
    return flask.redirect(flask.url_for('todoist_api.summary_goals'))

def is_ajax_request() -> bool:
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
    )

def _get_filtered_summary_goals(user):
    from web_app.todoist import _get_filtered_summary_goals as get_goals
    return get_goals(user)

def _get_completed_goals(user):
    from web_app.todoist import _get_completed_goals as get_goals
    return get_goals(user)

def _goals_to_blocks(goals):
    from web_app.todoist import _goals_to_blocks as to_blocks
    return to_blocks(goals)

def _completed_goals_to_blocks(goals):
    from web_app.todoist import _completed_goals_to_blocks as to_blocks
    return to_blocks(goals)

def _todoist_page_size() -> int:
    from web_app.todoist import PAGE_SIZE
    return PAGE_SIZE

def goals_fragment_response():
    view = request.headers.get('X-Todoist-View', 'summary')
    page_size = _todoist_page_size()

    if view == 'completed':
        goals = _get_completed_goals(cur_user())
        html = render_template(
            'completed_goals.html',
            dated_goal_blocks=_completed_goals_to_blocks(goals[:page_size]),
        )
    else:
        goals, all_goals = _get_filtered_summary_goals(cur_user())
        html = render_template(
            'summary_goals.html',
            dated_goal_blocks=_goals_to_blocks(goals[:page_size]),
            all_goals=all_goals,
        )

    return flask.jsonify(
        success=True,
        html=html,
        has_more=len(goals) > page_size,
        view=view,
    )

def goal_error_response(message: str):
    if is_ajax_request():
        return flask.jsonify(success=False, error=message), 400

    flask.flash(message, category='error')
    return get_default_redirect()

@goals_api.route('/new', methods=["POST"])
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def new_goal():
    name = from_req('name')
    if not name:
        return goal_error_response('Goal name cannot be empty')

    description = from_req('description')
    parent_id = request.form.get('parent_id')

    tld = DataInterface().load_goals(cur_user())
    goal_id = 0 if not tld.goals else max(tld.goals.keys()) + 1
    goal = Goal(id=goal_id,
                name=name,
                state=GoalState.ACTIVE,
                description=description,
                creation_date=datetime.now(),
                parent=int(parent_id) if parent_id else None)
    tld.goals[goal_id] = goal

    if parent_id:
        tld.goals[int(parent_id)].children.append(goal_id)

    DataInterface().save_goals(tld, cur_user())

    if is_ajax_request():
        return goals_fragment_response()

    return get_default_redirect()

@goals_api.route('/fail', methods=["GET"])
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def fail_goal():
    req_data = request.args

    goal_id = int(req_data['goal_id'])
    tld = DataInterface().load_goals(cur_user())
    tld.goals[goal_id].state = GoalState.FAILED
    DataInterface().save_goals(tld, cur_user())

    if is_ajax_request():
        return goals_fragment_response()

    return get_default_redirect()

@goals_api.route('/log', methods=["POST"])
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def log_goal():
    goal_id = int(request.args['goal_id'])

    tld = DataInterface().load_goals(cur_user())
    goal = tld.goals[goal_id]
    today_date = datetime.now().date()
    today_date = today_date.strftime("%d/%m/%Y")
    goal.description += f"\n\n{'-'*10}\n{today_date}\n{from_req('log')}\n{'-'*10}"
    goal.last_modified = datetime.now()
    DataInterface().save_goals(tld, cur_user())

    if is_ajax_request():
        return goals_fragment_response()

    return get_default_redirect()

@goals_api.route('/toggle_state', methods=['POST'])
@limiter.limit("2/second", key_func=lambda: flask_login.current_user.id)
def toggle_goal_state():
    req_data = request.get_json()

    tld = DataInterface().load_goals(cur_user())
    goal = tld.goals[req_data['goal_id']]
    if goal.state == GoalState.ACTIVE:
        goal.state = GoalState.COMPLETED
        goal.completion_date = datetime.now()
    elif goal.state == GoalState.COMPLETED:
        goal.state = GoalState.ACTIVE
        goal.completion_date = None
    else:
        raise ValueError(f"Cannot toggle goal state for goal in state {goal.state}")

    DataInterface().save_goals(tld, cur_user())

    return flask.jsonify(success=True)

@goals_api.route('/edit', methods=["POST"])
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def edit_goal():
    name = from_req('name')
    if not name:
        return goal_error_response('Goal name cannot be empty')
    description = from_req('description')

    goal_id = int(request.args['goal_id'])

    tld = DataInterface().load_goals(cur_user())
    goal = tld.goals[goal_id]
    goal.name = name
    goal.description = description
    goal.last_modified = datetime.now()
    DataInterface().save_goals(tld, cur_user())

    if is_ajax_request():
        return goals_fragment_response()

    return get_default_redirect()

@goals_api.route('/delete', methods=["GET"])
@limiter.limit("1/second", key_func=lambda: flask_login.current_user.id)
def delete_goal():
    req_data = request.args

    goal_id = int(req_data['goal_id'])
    tld = DataInterface().load_goals(cur_user())
    goal = tld.goals[goal_id]

    # Remove from parent's children list
    if goal.parent is not None and goal.parent in tld.goals:
        parent = tld.goals[goal.parent]
        if goal_id in parent.children:
            parent.children.remove(goal_id)

    # Recursively delete all descendants
    def delete_descendants(gid):
        if gid not in tld.goals:
            return
        for child_id in list(tld.goals[gid].children):
            delete_descendants(child_id)
        tld.goals.pop(gid)

    delete_descendants(goal_id)
    DataInterface().save_goals(tld, cur_user())

    return get_default_redirect()
