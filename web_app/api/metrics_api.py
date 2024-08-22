import flask
import flask_login
import logging

from typing import *
from flask import request, Blueprint, render_template
from datetime import datetime
from flask_limiter import Limiter

from web_app.app_data import Metric, DataPoint
from web_app.data_interface import DataInterface
from web_app.helpers import limiter, from_req
from web_app.visualiser import plot_metric


metrics_api = Blueprint('metrics_api', __name__)

def get_default_redirect():
    return flask.redirect(flask.url_for('metrics_api.get_metrics'))

@metrics_api.route('/metrics', methods=['GET'])
@flask_login.login_required
@limiter.limit("2 per second")
def get_metrics():
    tld = DataInterface.instance().load_data(flask_login.current_user)
    metrics = list(tld.metrics.values())
    metrics.sort(key=lambda x: x.id)
    for metric in metrics:
        metric.data.sort(key=lambda x: x.date, reverse=True)

    return render_template('metrics_page.html', metrics=metrics)

@metrics_api.route('/metrics/new', methods=['POST'])
@flask_login.login_required
@limiter.limit("2 per second")
def new_metric():
    name = from_req('name')
    unit = from_req('units')
    description = from_req('description')

    if not name:
        flask.flash('Metric name cannot be empty', category='error')
        return get_default_redirect()

    data_interface = DataInterface.instance()
    tld = data_interface.load_data(flask_login.current_user)
    
    metric_id = 0 if not tld.metrics else max(tld.metrics.keys()) + 1
    tld.metrics[metric_id] = Metric(id=metric_id, 
                                    name=name, 
                                    data=[], 
                                    unit=unit, 
                                    description=description)
    data_interface.save_data(tld, flask_login.current_user)
    
    return get_default_redirect()

@metrics_api.route('/metrics/delete', methods=['GET'])
@flask_login.login_required
@limiter.limit("2 per second")
def delete_metric():
    metric_id = int(from_req('metric_id'))
    tld = DataInterface.instance().load_data(flask_login.current_user)
    tld.metrics.pop(metric_id)
    DataInterface.instance().save_data(tld, flask_login.current_user)

    return get_default_redirect()

@metrics_api.route('/metrics/edit', methods=['POST'])
@flask_login.login_required
@limiter.limit("2 per second")
def edit_metric():
    metric_id = int(from_req('metric_id'))
    name = from_req('name')

    if not name:
        flask.flash('Metric name cannot be empty', category='error')
        return get_default_redirect()

    unit = from_req('units')
    description = from_req('description')

    tld = DataInterface.instance().load_data(flask_login.current_user)
    metric = tld.metrics[metric_id]
    metric.name = name
    metric.unit = unit
    metric.description = description
    DataInterface.instance().save_data(tld, flask_login.current_user)

    return get_default_redirect()

@metrics_api.route('/metrics/log', methods=['POST'])
@flask_login.login_required
@limiter.limit("2 per second")
def log_metric():
    metric_id = int(from_req('metric_id'))
    try:
        value = float(from_req('value'))
    except ValueError:
        flask.flash('Value must be a number', category='error')
        return get_default_redirect()

    tld = DataInterface.instance().load_data(flask_login.current_user)
    metric = tld.metrics[metric_id]
    metric.data.append(DataPoint(date=datetime.now(), value=value))
    DataInterface.instance().save_data(tld, flask_login.current_user)

    return get_default_redirect()

@metrics_api.route('/metrics/visualise/<int:metric_id>', methods=['GET'])
@flask_login.login_required
@limiter.limit("1 per second")
def visualise_metric(metric_id: int):
    tld = DataInterface.instance().load_data(flask_login.current_user)
    metric = tld.metrics[metric_id]

    try:
        embeddable_plotly_html = plot_metric(metric)

        return render_template('metric_plot.html', plot=embeddable_plotly_html)
    except Exception as e:
        logging.error(f"Failed to visualise metric {metric_id}: {e}")
        flask.flash('Failed to visualise metric', category='error')

        return get_default_redirect()

