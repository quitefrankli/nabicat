from flask import Blueprint, render_template


simulations_api = Blueprint(
    'simulations_api',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/simulations')


@simulations_api.context_processor
def inject_app_name():
    return dict(app_name='Simulations')


@simulations_api.route('/', methods=['GET'])
def index():
    return render_template('simulations_page.html')


@simulations_api.route('/game-of-life', methods=['GET'])
def game_of_life():
    return render_template('game_of_life.html')
