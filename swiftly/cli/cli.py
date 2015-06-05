"""
Contains the CLI class that handles the original command line.
"""
"""
Copyright 2011-2013 Gregory Holt

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
from ConfigParser import Error as ConfigParserError, SafeConfigParser
import functools
import os
import sys
import tempfile
import textwrap
import time
import traceback

from swiftly import VERSION
from swiftly.cli.context import CLIContext
from swiftly.cli.iomanager import IOManager
from swiftly.cli.optionparser import OptionParser
from swiftly.client import ClientManager, DirectClient, LocalClient, \
    StandardClient


#: The list of CLICommand classes avaiable to CLI. You'll want to add any new
#: CLICommand you create to this list.
COMMANDS = [
    'swiftly.cli.auth.CLIAuth',
    'swiftly.cli.decrypt.CLIDecrypt',
    'swiftly.cli.delete.CLIDelete',
    'swiftly.cli.encrypt.CLIEncrypt',
    'swiftly.cli.fordo.CLIForDo',
    'swiftly.cli.get.CLIGet',
    'swiftly.cli.head.CLIHead',
    'swiftly.cli.help.CLIHelp',
    'swiftly.cli.ping.CLIPing',
    'swiftly.cli.post.CLIPost',
    'swiftly.cli.put.CLIPut',
    'swiftly.cli.tempurl.CLITempURL',
    'swiftly.cli.trans.CLITrans']

TRUE_VALUES = ['1', 'on', 't', 'true', 'y', 'yes']
"""A list of lowercase string values that equate to True."""


class CLI(object):

    """
    Handles the original command line.

    An example script is `swiftly` itself::

        #!/usr/bin/env python
        import sys
        import swiftly.cli
        sys.exit(swiftly.cli.CLI()())

    See the output of ``swiftly help`` for more information.

    :param commands: The list of commands available; if None
        :py:attr:`COMMANDS` will be used.
    """

    def __init__(self, commands=None):
        #: The overall CLIContext containing attributes generated by the
        #: initial main_options parsing.
        #:
        #: The available attributes are:
        #:
        #: ==============  ====================================================
        #: cdn             True if the CDN URL should be used instead of the
        #:                 default Storage URL.
        #: client_manager  The :py:class:`swiftly.client.manager.ClientManager`
        #:                 to use for obtaining clients.
        #: concurrency     Number of concurrent actions to allow.
        #: io_manager      The :py:class:`swiftly.cli.iomanager.IOManager` to
        #:                 use for input and output.
        #: eventlet        True if Eventlet is in use.
        #: original_args   The original args used by the CLI.
        #: original_begin  The original time.time() when the CLI was called.
        #: verbose         Function to call when you want to (optionally) emit
        #:                 verbose output. ``verbose(msg, *args)`` where the
        #:                 output will be constructed with ``msg % args``.
        #: verbosity       Level of verbosity. Just None or 1 right now.
        #: ==============  ====================================================
        self.context = CLIContext()
        self.context.verbose = None
        self.context.io_manager = IOManager()

        #: A dictionary of the available commands and their CLICommand
        #: instances.
        self.commands = {}
        if commands is None:
            commands = COMMANDS
        for command in commands:
            mod, cls = command.rsplit('.', 1)
            cls = getattr(__import__(mod, fromlist=[cls]), cls)
            inst = cls(self)
            self.commands[inst.name] = inst

        #: The main :py:class:`OptionParser`.
        self.option_parser = OptionParser(
            version=VERSION,
            usage="""
Usage: %prog [options] <command> [command_options] [args]

With all these main Swiftly options, you can also specify them in a
configuration file or in an environment variable.

The configuration file option name is the long name of the command line option
with any dashes replaced with underscores, for example:
--auth-url becomes auth_url

The environment variable name is the long name of the command line option in
uppercase prefixed with SWIFTLY_, for example:
--auth-url becomes SWIFTLY_AUTH_URL

Command line options override environment variables which override
configuration file variables.
            """.strip(),
            io_manager=self.context.io_manager)
        self.option_parser.add_option(
            '-h', dest='help', action='store_true',
            help='Shows this help text.')
        self.option_parser.add_option(
            '--conf', dest='conf', metavar='PATH',
            help='Path to the configuration file to use. Default: '
                 '~/.swiftly.conf')
        self.option_parser.add_option(
            '-A', '--auth-url', dest='auth_url', metavar='URL',
            help='URL to auth system, example: '
                 'https://identity.api.rackspacecloud.com/v2.0')
        self.option_parser.add_option(
            '-U', '--auth-user', dest='auth_user', metavar='USER',
            help='User name for auth system, example: test:tester')
        self.option_parser.add_option(
            '-K', '--auth-key', dest='auth_key', metavar='KEY',
            help='Key for auth system, example: testing')
        self.option_parser.add_option(
            '-T', '--auth-tenant', dest='auth_tenant', metavar='TENANT',
            help='Tenant name for auth system, example: test If not '
                 'specified and needed, the auth user will be used.')
        self.option_parser.add_option(
            '--auth-methods', dest='auth_methods', metavar='name[,name[...]]',
            help='Auth methods to use with the auth system, example: '
                 'auth2key,auth2password,auth2password_force_tenant,auth1 The '
                 'best order will try to be determined for you; but if you '
                 'notice it keeps making useless auth attempts and that '
                 'drives you crazy, you can override that here. All the '
                 'available auth methods are listed in the example.')
        self.option_parser.add_option(
            '--region', dest='region', metavar='VALUE',
            help='Region to use, if supported by auth, example: DFW Default: '
                 'default region specified by the auth response.')
        self.option_parser.add_option(
            '-D', '--direct', dest='direct', metavar='PATH',
            help='Uses direct connect method to access Swift. Requires access '
                 'to rings and backend servers. The PATH is the account '
                 'path, example: /v1/AUTH_test')
        self.option_parser.add_option(
            '-L', '--local', dest='local', metavar='PATH',
            help='Uses the local file system method to access a fake Swift. '
                 'The PATH is the path on the local file system where the '
                 'fake Swift stores its data.')
        self.option_parser.add_option(
            '-P', '--proxy', dest='proxy', metavar='URL',
            help='Uses the given HTTP proxy URL.')
        self.option_parser.add_option(
            '-S', '--snet', dest='snet', action='store_true',
            help='Prepends the storage URL host name with "snet-". Mostly '
                 'only useful with Rackspace Cloud Files and Rackspace '
                 'ServiceNet.')
        self.option_parser.add_option(
            '--no-snet', dest='no_snet', action='store_true',
            help='Disables the above snet value if it had been set true in '
                 'the environment or configuration file.')
        self.option_parser.add_option(
            '-R', '--retries', dest='retries', metavar='INTEGER',
            help='Indicates how many times to retry the request on a server '
                 'error. Default: 4')
        self.option_parser.add_option(
            '-C', '--cache-auth', dest='cache_auth', action='store_true',
            help='If set true, the storage URL and auth token are cached in '
                 'your OS temporary directory as <user>.swiftly for reuse. If '
                 'there are already cached values, they are used without '
                 'authenticating first.')
        self.option_parser.add_option(
            '--no-cache-auth', dest='no_cache_auth', action='store_true',
            help='Disables the above cache-auth value if it had been set '
                 'true in the environment or configuration file.')
        self.option_parser.add_option(
            '--cdn', dest='cdn', action='store_true',
            help='Directs requests to the CDN management interface.')
        self.option_parser.add_option(
            '--no-cdn', dest='no_cdn', action='store_true',
            help='Disables the above cdn value if it had been set true in the '
                 'environment or configuration file.')
        self.option_parser.add_option(
            '--concurrency', dest='concurrency', metavar='INTEGER',
            help='Sets the the number of actions that can be done '
                 'simultaneously when possible (currently requires using '
                 'Eventlet too). Default: 1 Note that some nested actions may '
                 'amplify the number of concurrent actions. For instance, a '
                 'put of an entire directory will use up to this number of '
                 'concurrent actions. A put of a segmented object will use up '
                 'to this number of concurrent actions. But, if a directory '
                 'structure put is uploading segmented objects, this nesting '
                 'could cause up to INTEGER * INTEGER concurrent actions.')
        self.option_parser.add_option(
            '--eventlet', dest='eventlet', action='store_true',
            help='Enables Eventlet, if installed. This is disabled by default '
                 'if Eventlet is not installed or is less than version 0.11.0 '
                 '(because older Swiftly+Eventlet tends to use excessive CPU.')
        self.option_parser.add_option(
            '--no-eventlet', dest='no_eventlet', action='store_true',
            help='Disables Eventlet, even if installed and version 0.11.0 or '
                 'greater.')
        self.option_parser.add_option(
            '-v', '--verbose', dest='verbose', action='store_true',
            help='Causes output to standard error indicating actions being '
                 'taken. These output lines will be prefixed with VERBOSE and '
                 'will also include the number of seconds elapsed since '
                 'the command started.')
        self.option_parser.add_option(
            '--no-verbose', dest='no_verbose', action='store_true',
            help='Disables the above verbose value if it had been set true in '
                 'the environment or configuration file.')
        self.option_parser.add_option(
            '-O', '--direct-object-ring',
            dest='direct_object_ring', metavar='PATH',
            help='The current object ring of the cluster being pinged. This '
                 'will enable direct client to use this ring for all the '
                 'queries. Use of this also requires the main Swift code  '
                 'is installed and importable.')

        self.option_parser.raw_epilog = 'Commands:\n'
        for name in sorted(self.commands):
            command = self.commands[name]
            lines = command.option_parser.get_usage().split('\n')
            main_line = '  ' + lines[0].split(']', 1)[1].strip()
            for x in xrange(4):
                lines.pop(0)
            for x, line in enumerate(lines):
                if not line:
                    lines = lines[:x]
                    break
            if len(main_line) < 24:
                initial_indent = main_line + ' ' * (24 - len(main_line))
            else:
                self.option_parser.raw_epilog += main_line + '\n'
                initial_indent = ' ' * 24
            self.option_parser.raw_epilog += textwrap.fill(
                ' '.join(lines), width=79, initial_indent=initial_indent,
                subsequent_indent=' ' * 24) + '\n'

    def __call__(self, args=None):
        options, args = self._parse_args(args)
        if not options:
            return 1
        return self._perform_command(args)

    def _parse_args(self, args=None):
        self.context.original_begin = time.time()
        self.context.original_args = args if args is not None else sys.argv[1:]
        self.option_parser.disable_interspersed_args()
        try:
            options, args = self.option_parser.parse_args(
                self.context.original_args)
        except UnboundLocalError:
            # Happens sometimes with an error handler that doesn't raise its
            # own exception. We'll catch the error below with
            # error_encountered.
            pass
        self.option_parser.enable_interspersed_args()
        if self.option_parser.error_encountered:
            return None, None
        if options.version:
            self.option_parser.print_version()
            return None, None
        if not args or options.help:
            self.option_parser.print_help()
            return None, None
        self.context.original_main_args = self.context.original_args[
            :-len(args)]

        self.context.conf = {}
        if options.conf is None:
            options.conf = os.environ.get('SWIFTLY_CONF', '~/.swiftly.conf')
        try:
            conf_parser = SafeConfigParser()
            conf_parser.read(os.path.expanduser(options.conf))
            for section in conf_parser.sections():
                self.context.conf[section] = dict(conf_parser.items(section))
        except ConfigParserError:
            pass

        for option_name in (
                'auth_url', 'auth_user', 'auth_key', 'auth_tenant',
                'auth_methods', 'region', 'direct', 'local', 'proxy', 'snet',
                'no_snet', 'retries', 'cache_auth', 'no_cache_auth', 'cdn',
                'no_cdn', 'concurrency', 'eventlet', 'no_eventlet', 'verbose',
                'no_verbose', 'direct_object_ring'):
            self._resolve_option(options, option_name, 'swiftly')
        for option_name in (
                'snet', 'no_snet', 'cache_auth', 'no_cache_auth', 'cdn',
                'no_cdn', 'eventlet', 'no_eventlet', 'verbose', 'no_verbose'):
            if isinstance(getattr(options, option_name), basestring):
                setattr(
                    options, option_name,
                    getattr(options, option_name).lower() in TRUE_VALUES)
        for option_name in ('retries', 'concurrency'):
            if isinstance(getattr(options, option_name), basestring):
                setattr(
                    options, option_name, int(getattr(options, option_name)))
        if options.snet is None:
            options.snet = False
        if options.no_snet is None:
            options.no_snet = False
        if options.retries is None:
            options.retries = 4
        if options.cache_auth is None:
            options.cache_auth = False
        if options.no_cache_auth is None:
            options.no_cache_auth = False
        if options.cdn is None:
            options.cdn = False
        if options.no_cdn is None:
            options.no_cdn = False
        if options.concurrency is None:
            options.concurrency = 1
        if options.eventlet is None:
            options.eventlet = False
        if options.no_eventlet is None:
            options.no_eventlet = False
        if options.verbose is None:
            options.verbose = False
        if options.no_verbose is None:
            options.no_verbose = False

        self.context.eventlet = None
        if options.eventlet:
            self.context.eventlet = True
        if options.no_eventlet:
            self.context.eventlet = False
        if self.context.eventlet is None:
            self.context.eventlet = False
            try:
                import eventlet
                # Eventlet 0.11.0 fixed the CPU bug
                if eventlet.__version__ >= '0.11.0':
                    self.context.eventlet = True
            except ImportError:
                pass

        subprocess_module = None
        if self.context.eventlet:
            try:
                import eventlet.green.subprocess
                subprocess_module = eventlet.green.subprocess
            except ImportError:
                pass
        if subprocess_module is None:
            import subprocess
            subprocess_module = subprocess
        self.context.io_manager.subprocess_module = subprocess_module

        if options.verbose:
            self.context.verbosity = 1
            self.context.verbose = self._verbose
            self.context.io_manager.verbose = functools.partial(
                self._verbose, skip_sub_command=True)

        options.retries = int(options.retries)
        if args and args[0] == 'help':
            return options, args
        elif options.local:
            self.context.client_manager = ClientManager(
                LocalClient, local_path=options.local, verbose=self._verbose)
        elif options.direct:
            self.context.client_manager = ClientManager(
                DirectClient, swift_proxy_storage_path=options.direct,
                attempts=options.retries + 1, eventlet=self.context.eventlet,
                verbose=self._verbose,
                direct_object_ring=options.direct_object_ring)
        else:
            auth_cache_path = None
            if options.cache_auth:
                auth_cache_path = os.path.join(
                    tempfile.gettempdir(),
                    '%s.swiftly' % os.environ.get('USER', 'user'))
            if not options.auth_url:
                with self.context.io_manager.with_stderr() as fp:
                    fp.write('No Auth URL has been given.\n')
                    fp.flush()
                return None, None
            self.context.client_manager = ClientManager(
                StandardClient, auth_methods=options.auth_methods,
                auth_url=options.auth_url, auth_tenant=options.auth_tenant,
                auth_user=options.auth_user, auth_key=options.auth_key,
                auth_cache_path=auth_cache_path, region=options.region,
                snet=options.snet, attempts=options.retries + 1,
                eventlet=self.context.eventlet, verbose=self._verbose,
                http_proxy=options.proxy)

        self.context.cdn = options.cdn
        self.context.concurrency = int(options.concurrency)

        return options, args

    def _resolve_option(self, options, option_name, section_name):
        """Resolves an option value into options.

        Sets options.<option_name> to a resolved value. Any value
        already in options overrides a value in os.environ which
        overrides self.context.conf.

        :param options: The options instance as returned by optparse.
        :param option_name: The name of the option, such as
            ``auth_url``.
        :param section_name: The name of the section, such as
            ``swiftly``.
        """
        if getattr(options, option_name, None) is not None:
            return
        if option_name.startswith(section_name + '_'):
            environ_name = option_name.upper()
            conf_name = option_name[len(section_name) + 1:]
        else:
            environ_name = (section_name + '_' + option_name).upper()
            conf_name = option_name
        setattr(
            options, option_name,
            os.environ.get(
                environ_name,
                (self.context.conf.get(section_name, {})).get(conf_name)))

    def _perform_command(self, args):
        command = args[0]
        if command == 'for':
            command = 'fordo'
        if command not in self.commands:
            with self.context.io_manager.with_stderr() as fp:
                fp.write('ERROR unknown command %r\n' % args[0])
                fp.flush()
            return 1
        try:
            self.commands[command](args[1:])
        except Exception as err:
            if hasattr(err, 'text'):
                if err.text:
                    with self.context.io_manager.with_stderr() as fp:
                        fp.write('ERROR ')
                        fp.write(err.text)
                        fp.write('\n')
                        fp.flush()
            else:
                with self.context.io_manager.with_stderr() as fp:
                    fp.write(traceback.format_exc())
                    fp.write('\n')
                    fp.flush()
            return getattr(err, 'code', 1)
        return 0

    def _verbose(self, msg, *args, **kwargs):
        if self.context.verbosity:
            skip_sub_command = kwargs.get('skip_sub_command', False)
            with self.context.io_manager.with_debug(
                    skip_sub_command=skip_sub_command) as fp:
                try:
                    fp.write(
                        'VERBOSE %.02f ' %
                        (time.time() - self.context.original_begin))
                    fp.write(msg % args)
                except TypeError as err:
                    raise TypeError('%s: %r %r' % (err, msg, args))
                fp.write('\n')
                fp.flush()
