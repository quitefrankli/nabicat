from flask import Blueprint, render_template

from web_app.helpers import register_app_name


simulations_api = Blueprint(
    'simulations_api',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/simulations')


register_app_name(simulations_api, 'Simulations')


@simulations_api.route('/', methods=['GET'])
def index():
    return render_template('simulations_page.html')


@simulations_api.route('/game-of-life', methods=['GET'])
def game_of_life():
    return render_template('game_of_life.html')


@simulations_api.route('/astar', methods=['GET'])
def astar():
    return render_template('astar.html')
