# This file is part of flask_tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.

from functools import wraps

from flask import current_app, request
from werkzeug.exceptions import BadRequest
from werkzeug.routing import BaseConverter

from trytond import __version__ as trytond_version
from trytond.config import config
from trytond.exceptions import ConcurrencyException, UserError, UserWarning

trytond_version = tuple(map(int, trytond_version.split('.')))
__version__ = '0.11.3'
__all__ = ['Tryton', 'tryton_transaction']


# Start jsl patch
from trytond.transaction import Transaction
from contextlib import contextmanager
@contextmanager
def conditional_transaction_for_tests(database, user, readonly=True, context=None):
    """
    Start a new transaction, unless in the context of tests, and
    transaction is already running.
    """
    need_new_transaction = (
        not config.get('web', 'testing_flask') or
        not Transaction().user  # test if started
    )
    if need_new_transaction:
        with Transaction().start(
                database, user, readonly=readonly, context=context
        ) as transaction:
            yield transaction
    else:
        @contextmanager
        def dummy_manager():
            yield Transaction()

        with dummy_manager() as dummy:
            yield dummy
# end jsl patch



def retry_transaction(func):
    """Decorator to retry a transaction if failed. The decorated method
    will be run retry times in case of DatabaseOperationalError.
    """
    from trytond import backend
    from trytond.transaction import Transaction
    try:
        DatabaseOperationalError = backend.DatabaseOperationalError
    except AttributeError:
        DatabaseOperationalError = backend.get('DatabaseOperationalError')

    @wraps(func)
    def wrapper(*args, **kwargs):
        tryton = current_app.extensions['Tryton']
        retry = tryton.database_retry
        for count in range(retry, -1, -1):
            try:
                return func(*args, **kwargs)
            except DatabaseOperationalError:
                if count and not Transaction().readonly:
                    continue
                raise
    return wrapper


class Tryton(object):
    "Control the Tryton integration to one or more Flask applications."
    def __init__(self, app=None, configure_jinja=False):
        self.context_callback = None
        self.database_retry = None
        self._configure_jinja = configure_jinja
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        "Initialize an application for the use with this Tryton setup."
        database = app.config.setdefault('TRYTON_DATABASE', None)
        user = app.config.setdefault('TRYTON_USER', 0)
        configfile = app.config.setdefault('TRYTON_CONFIG', None)

        config.update_etc(configfile)

        from trytond.pool import Pool
        from trytond.transaction import Transaction

        self.database_retry = config.getint('database', 'retry')
        self.pool = Pool(database)
        with conditional_transaction_for_tests(database, user, readonly=True): #jsl
            self.pool.init()

        if not hasattr(app, 'extensions'):
            app.extensions = {}
        app.extensions['Tryton'] = self
        app.url_map.converters['record'] = RecordConverter
        app.url_map.converters['records'] = RecordsConverter
        if self._configure_jinja:
            app.jinja_env.filters.update(
                numberformat=self.format_number,
                dateformat=self.format_date,
                currencyformat=self.format_currency,
                timedeltaformat=self.format_timedelta,
                )

    def default_context(self, callback):
        "Set the callback for the default transaction context"
        self.context_callback = callback
        return callback

    @property
    def language(self):
        "Return a language instance for the current request"
        from trytond.transaction import Transaction
        Lang = self.pool.get('ir.lang')
        # Do not use Transaction.language as it fallbacks to default language
        language = Transaction().context.get('language')
        if not language and request:
            language = request.accept_languages.best_match(
                Lang.get_translatable_languages())
        return Lang.get(language)

    def format_date(self, value, lang=None, *args, **kwargs):
        from trytond.report import Report
        if lang is None:
            lang = self.language
        return Report.format_date(value, lang, *args, **kwargs)

    def format_number(self, value, lang=None, *args, **kwargs):
        from trytond.report import Report
        if lang is None:
            lang = self.language
        return Report.format_number(value, lang, *args, **kwargs)

    def format_currency(self, value, currency, lang=None, *args, **kwargs):
        from trytond.report import Report
        if lang is None:
            lang = self.language
        return Report.format_currency(value, lang, currency, *args, **kwargs)

    def format_timedelta(
            self, value, converter=None, lang=None, *args, **kwargs):
        from trytond.report import Report
        if not hasattr(Report, 'format_timedelta'):
            return str(value)
        if lang is None:
            lang = self.language
        return Report.format_timedelta(
            value, converter=converter, lang=lang, *args, **kwargs)

    def _readonly(self):
        return not (request
            and request.method in ('PUT', 'POST', 'DELETE', 'PATCH'))

    @staticmethod
    def transaction(readonly=None, user=None, context=None):
        """Decorator to run inside a Tryton transaction.
        The decorated method could be run multiple times in case of
        database operational error.

        If readonly is None then the transaction will be readonly except for
        PUT, POST, DELETE and PATCH request methods.

        If user is None then TRYTON_USER will be used.

        readonly, user and context can also be callable.
        """
        from trytond import backend
        from trytond.cache import Cache
        from trytond.transaction import Transaction
        try:
            DatabaseOperationalError = backend.DatabaseOperationalError
        except AttributeError:
            DatabaseOperationalError = backend.get('DatabaseOperationalError')

        def get_value(value):
            return value() if callable(value) else value

        def instanciate(value):
            if isinstance(value, _BaseProxy):
                return value()
            return value

        def decorator(func):
            @retry_transaction
            @wraps(func)
            def wrapper(*args, **kwargs):
                tryton = current_app.extensions['Tryton']
                database = current_app.config['TRYTON_DATABASE']
                if (5, 1) > trytond_version:
                    #jsl
                    with conditional_transaction_for_tests(database, 0):
                        Cache.clean(database)
                if user is None:
                    transaction_user = get_value(
                        int(current_app.config['TRYTON_USER']))
                else:
                    transaction_user = get_value(user)

                if readonly is None:
                    is_readonly = get_value(tryton._readonly)
                else:
                    is_readonly = get_value(readonly)

                transaction_context = {}
                if tryton.context_callback or context:
                    #jsl
                    with conditional_transaction_for_tests(
                        database, transaction_user, readonly=True
                    ):
                        if tryton.context_callback:
                            transaction_context = tryton.context_callback()
                        transaction_context.update(get_value(context) or {})

                transaction_context.setdefault('_request', {}).update({
                        'remote_addr': request.remote_addr,
                        'http_host': request.environ.get('HTTP_HOST'),
                        'scheme': request.scheme,
                        'is_secure': request.is_secure,
                        } if request else {})

                #jsl
                with conditional_transaction_for_tests(
                    database, transaction_user, readonly=is_readonly,
                    context=transaction_context
                ) as transaction:
                    try:
                        result = func(*map(instanciate, args),
                            **dict((n, instanciate(v))
                                for n, v in kwargs.items()))
                        if (hasattr(transaction, 'cursor')
                                and not is_readonly):
                            transaction.cursor.commit()
                    except DatabaseOperationalError:
                        raise
                    except Exception as e:
                        if isinstance(e, (
                                    UserError,
                                    UserWarning,
                                    ConcurrencyException)):
                            raise BadRequest(e.message)
                        raise
                    if (5, 1) > trytond_version:
                        Cache.resets(database)
                from trytond.worker import run_task
                while transaction.tasks:
                    task_id = transaction.tasks.pop()
                    run_task(tryton.pool, task_id)
                return result
            return wrapper
        return decorator


tryton_transaction = Tryton.transaction


class _BaseProxy(object):
    pass


class _RecordsProxy(_BaseProxy):
    def __init__(self, model, ids):
        self.model = model
        self.ids = list(ids)

    def __iter__(self):
        return iter(self.ids)

    def __call__(self):
        tryton = current_app.extensions['Tryton']
        Model = tryton.pool.get(self.model)
        return Model.browse(self.ids)


class _RecordProxy(_RecordsProxy):
    def __init__(self, model, id):
        super(_RecordProxy, self).__init__(model, [id])

    def __int__(self):
        return self.ids[0]

    def __call__(self):
        return super(_RecordProxy, self).__call__()[0]


class RecordConverter(BaseConverter):
    """This converter accepts record id of model::

        Rule('/page/<record("res.user"):user>')"""
    regex = r'\d+'

    def __init__(self, map, model):
        super(RecordConverter, self).__init__(map)
        self.model = model

    def to_python(self, value):
        return _RecordProxy(self.model, int(value))

    def to_url(self, value):
        return str(int(value))


class RecordsConverter(BaseConverter):
    """This converter accepts record ids of model::

        Rule('/page/<records("res.user"):users>')"""
    regex = r'\d+(,\d+)*'

    def __init__(self, map, model):
        super(RecordsConverter, self).__init__(map)
        self.model = model

    def to_python(self, value):
        return _RecordsProxy(self.model, map(int, value.split(',')))

    def to_url(self, value):
        return ','.join(map(str, map(int, value)))
