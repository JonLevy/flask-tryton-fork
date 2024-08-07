Flask-Tryton
============

Adds Tryton support to Flask application.

By default transactions are readonly except for PUT, POST, DELETE and PATCH
request methods.
It provides also 2 routing converters `record` and `records`.

Setting the `configure_jinja` flag adds the following filters on jinja
templates: `numberformat`, `dateformat`, `currencyformat` and
`timedeltaformat`. The filters apply the same formatting as Tryton reports.

Nutshell
--------

>>> from flask import Flask
>>> from flask_tryton import Tryton
>>> app = Flask(__name__)
>>> app.config['TRYTON_DATABASE'] = 'test'
>>> tryton = Tryton(app, configure_jinja=True)
>>> User = tryton.pool.get('res.user')
>>> @tryton.default_context
... def default_context():
...     return User.get_preferences(context_only=True)
>>> @app.route('/')
... @tryton.transaction()
... def hello():
...     user, = User.search([('login', '=', 'admin')])
...     return '%s, Hello World!' % user.name
>>> @app.route('/user/<record("res.user"):user>')
... @tryton.transaction()
... def user(user):
...     return user.name
>>> @app.route('/users/<records("res.user"):users>')
... @tryton.transaction()
... def users(users):
...     return ', '.join(u.name for u in users)
>>> app.run()

There are three configuration options available:

* `TRYTON_DATABASE`: the Tryton's database to connect.
* `TRYTON_USER`: the Tryton user id to use, by default `0` (aka `root`).
* `TRYTON_CONFIG`: the optional path to the Tryton's configuration.

To report issues please visit the `flask_tryton bugtracker`_.

.. _flask_tryton bugtracker: http://bugs.tryton.org/flask-tryton
