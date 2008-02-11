# -*- test-case-name: twisted.test.test_application,twisted.test.test_twistd -*-
# Copyright (c) 2001-2008 Twisted Matrix Laboratories.
# See LICENSE for details.

import sys, os, pdb, getpass, traceback, signal, warnings

from twisted.python import runtime, log, usage, failure, util, logfile
from twisted.persisted import sob
from twisted.application import service, reactors
from twisted.internet import defer
from twisted import copyright

# Expose the new implementation of installReactor at the old location.
from twisted.application.reactors import installReactor
from twisted.application.reactors import NoSuchReactor



class _BasicProfiler(object):
    """
    @ivar saveStats: if C{True}, save the stats information instead of the
        human readable format
    @type saveStats: C{bool}

    @ivar profileOutput: the name of the file use to print profile data.
    @type profileOutput: C{str}
    """

    def __init__(self, profileOutput, saveStats):
        self.profileOutput = profileOutput
        self.saveStats = saveStats


    def _reportImportError(self, module, e):
        """
        Helper method to report an import error with a profile module. This
        has to be explicit because some of these modules are removed by
        distributions due to them being non-free.
        """
        s = "Failed to import module %s: %s" % (module, e)
        s += """
This is most likely caused by your operating system not including
the module due to it being non-free. Either do not use the option
--profile, or install the module; your operating system vendor
may provide it in a separate package.
"""
        raise SystemExit(s)



class ProfileRunner(_BasicProfiler):
    """
    Runner for the standard profile module.
    """

    def run(self, reactor):
        """
        Run reactor under the standard profiler.
        """
        try:
            import profile
        except ImportError, e:
            self._reportImportError("profile", e)

        p = profile.Profile()
        p.runcall(reactor.run)
        if self.saveStats:
            p.dump_stats(self.profileOutput)
        else:
            tmp, sys.stdout = sys.stdout, open(self.profileOutput, 'a')
            try:
                p.print_stats()
            finally:
                sys.stdout, tmp = tmp, sys.stdout
                tmp.close()



class HotshotRunner(_BasicProfiler):
    """
    Runner for the hotshot profile module.
    """

    def run(self, reactor):
        """
        Run reactor under the hotshot profiler.
        """
        try:
            import hotshot.stats
        except (ImportError, SystemExit), e:
            # Certain versions of Debian (and Debian derivatives) raise
            # SystemExit when importing hotshot if the "non-free" profiler
            # module is not installed.  Someone eventually recognized this
            # as a bug and changed the Debian packaged Python to raise
            # ImportError instead.  Handle both exception types here in
            # order to support the versions of Debian which have this
            # behavior.  The bug report which prompted the introduction of
            # this highly undesirable behavior should be available online at
            # <http://bugs.debian.org/cgi-bin/bugreport.cgi?bug=334067>. 
            # There seems to be no corresponding bug report which resulted
            # in the behavior being removed. -exarkun
            self._reportImportError("hotshot", e)

        # this writes stats straight out
        p = hotshot.Profile(self.profileOutput)
        p.runcall(reactor.run)
        if self.saveStats:
            # stats are automatically written to file, nothing to do
            return
        else:
            s = hotshot.stats.load(self.profileOutput)
            s.strip_dirs()
            s.sort_stats(-1)
            if getattr(s, 'stream', None) is not None:
                # Python 2.5 and above supports a stream attribute
                s.stream = open(self.profileOutput, 'w')
                s.print_stats()
                s.stream.close()
            else:
                # But we have to use a trick for Python < 2.5
                tmp, sys.stdout = sys.stdout, open(self.profileOutput, 'w')
                try:
                    s.print_stats()
                finally:
                    sys.stdout, tmp = tmp, sys.stdout
                    tmp.close()



class AppProfiler(object):
    """
    Class which selects a specific profile runner based on configuration
    options.

    @ivar profiler: the name of the selected profiler.
    @type profiler: C{str}
    """
    profilers = {"profile": ProfileRunner, "hotshot": HotshotRunner}

    def __init__(self, options):
        saveStats = options.get("savestats", False)
        profileOutput = options.get("profile", None)
        self.profiler = options.get("profiler", None)
        if options.get("nothotshot", False):
            warnings.warn("The --nothotshot option is deprecated. Please "
                          "specify the profiler name using the --profiler "
                          "option", category=DeprecationWarning)
            self.profiler = "profile"
        if self.profiler in self.profilers:
            profiler = self.profilers[self.profiler](profileOutput, saveStats)
            self.run = profiler.run
        else:
            raise SystemExit("Unsupported profiler name: %s" % (self.profiler,))



def runWithProfiler(reactor, config):
    """
    DEPRECATED in Twisted 2.6.

    Run reactor under standard profiler.
    """
    warnings.warn("runWithProfiler is deprecated since Twisted 2.6. "
                  "Use ProfileRunner instead.", DeprecationWarning, 2)
    item = AppProfiler(config)
    return item.run(reactor)



def runWithHotshot(reactor, config):
    """
    DEPRECATED in Twisted 2.6.

    Run reactor under hotshot profiler.
    """
    warnings.warn("runWithHotshot is deprecated since Twisted 2.6. "
                  "Use HotshotRunner instead.", DeprecationWarning, 2)
    item = AppProfiler(config)
    return item.run(reactor)



def fixPdb():
    def do_stop(self, arg):
        self.clear_all_breaks()
        self.set_continue()
        from twisted.internet import reactor
        reactor.callLater(0, reactor.stop)
        return 1

    def help_stop(self):
        print """stop - Continue execution, then cleanly shutdown the twisted reactor."""

    def set_quit(self):
        os._exit(0)

    pdb.Pdb.set_quit = set_quit
    pdb.Pdb.do_stop = do_stop
    pdb.Pdb.help_stop = help_stop



def runReactorWithLogging(config, oldstdout, oldstderr, profiler=None):
    """
    Start the reactor, using profiling if specified by the configuration, and
    log any error happening in the process.

    @param config: configuration of the twistd application.
    @type config: L{ServerOptions}

    @param oldstdout: initial value of C{sys.stdout}.
    @type oldstdout: C{file}

    @param oldstderr: initial value of C{sys.stderr}.
    @type oldstderr: C{file}

    @param profiler: object used to run the reactor with profiling.
    @type profiler: L{AppProfiler}
    """
    from twisted.internet import reactor
    try:
        if config['profile']:
            if profiler is not None:
                profiler.run(reactor)
            else:
                # Backward compatible code
                if not config['nothotshot']:
                    runWithHotshot(reactor, config)
                else:
                    runWithProfiler(reactor, config)
        elif config['debug']:
            sys.stdout = oldstdout
            sys.stderr = oldstderr
            if runtime.platformType == 'posix':
                signal.signal(signal.SIGUSR2, lambda *args: pdb.set_trace())
                signal.signal(signal.SIGINT, lambda *args: pdb.set_trace())
            fixPdb()
            pdb.runcall(reactor.run)
        else:
            reactor.run()
    except:
        if config['nodaemon']:
            file = oldstdout
        else:
            file = open("TWISTD-CRASH.log",'a')
        traceback.print_exc(file=file)
        file.flush()



def getPassphrase(needed):
    if needed:
        return getpass.getpass('Passphrase: ')
    else:
        return None



def getSavePassphrase(needed):
    if needed:
        passphrase = util.getPassword("Encryption passphrase: ")
    else:
        return None



class ApplicationRunner(object):
    """
    An object which helps running an application based on a config object.

    Subclass me and implement preApplication and postApplication
    methods. postApplication generally will want to run the reactor
    after starting the application.

    @ivar config: The config object, which provides a dict-like interface.

    @ivar application: Available in postApplication, but not
       preApplication. This is the application object.

    @ivar profilerFactory: Factory for creating a profiler object, able to
        profile the application if options are set accordingly.

    @ivar profiler: Instance provided by C{profilerFactory}.
    """
    profilerFactory = AppProfiler

    def __init__(self, config):
        self.config = config
        self.profiler = self.profilerFactory(config)


    def run(self):
        """
        Run the application.
        """
        self.preApplication()
        self.application = self.createOrGetApplication()

        # Later, try adapting self.application to ILogObserverFactory or
        # whatever and getting an observer from it, instead.  Fall back to
        # self.getLogObserver if the adaption fails.
        self.startLogging(self.getLogObserver())

        self.postApplication()


    def preApplication(self):
        """
        Override in subclass.

        This should set up any state necessary before loading and
        running the Application.
        """
        raise NotImplementedError()


    def startLogging(self, observer):
        """
        Initialize the logging system.

        @param observer: The observer to add to the logging system.
        """
        log.startLoggingWithObserver(observer)
        sys.stdout.flush()
        initialLog()


    def getLogObserver(self):
        """
        Create a log observer to be added to the logging system before running
        this application.
        """
        raise NotImplementedError()


    def postApplication(self):
        """
        Override in subclass.

        This will be called after the application has been loaded (so
        the C{application} attribute will be set). Generally this
        should start the application and run the reactor.
        """
        raise NotImplementedError


    def createOrGetApplication(self):
        """
        Create or load an Application based on the parameters found in the
        given L{ServerOptions} instance.

        If a subcommand was used, the L{service.IServiceMaker} that it
        represents will be used to construct a service to be added to
        a newly-created Application.

        Otherwise, an application will be loaded based on parameters in
        the config.
        """
        if self.config.subCommand:
            # If a subcommand was given, it's our responsibility to create
            # the application, instead of load it from a file.

            # loadedPlugins is set up by the ServerOptions.subCommands
            # property, which is iterated somewhere in the bowels of
            # usage.Options.
            plg = self.config.loadedPlugins[self.config.subCommand]
            ser = plg.makeService(self.config.subOptions)
            application = service.Application(plg.tapname)
            ser.setServiceParent(application)
        else:
            passphrase = getPassphrase(self.config['encrypted'])
            application = getApplication(self.config, passphrase)
        return application



def getApplication(config, passphrase):
    s = [(config[t], t)
           for t in ['python', 'xml', 'source', 'file'] if config[t]][0]
    filename, style = s[0], {'file':'pickle'}.get(s[1],s[1])
    try:
        log.msg("Loading %s..." % filename)
        application = service.loadApplication(filename, style, passphrase)
        log.msg("Loaded.")
    except Exception, e:
        s = "Failed to load application: %s" % e
        if isinstance(e, KeyError) and e.args[0] == "application":
            s += """
Could not find 'application' in the file. To use 'twistd -y', your .tac
file must create a suitable object (e.g., by calling service.Application())
and store it in a variable named 'application'. twistd loads your .tac file
and scans the global variables for one of this name.

Please read the 'Using Application' HOWTO for details.
"""
        traceback.print_exc(file=log.logfile)
        log.msg(s)
        log.deferr()
        sys.exit('\n' + s + '\n')
    return application



def reportProfile(report_profile, name):
    """
    DEPRECATED since Twisted 2.6. This does nothing.
    """
    warnings.warn("reportProfile is deprecated and a no-op since Twisted 2.6.",
                  category=DeprecationWarning)



def _reactorZshAction():
    return "(%s)" % " ".join([r.shortName for r in reactors.getReactorTypes()])

class ReactorSelectionMixin:
    """
    Provides options for selecting a reactor to install.
    """
    zsh_actions = {"reactor" : _reactorZshAction}
    messageOutput = sys.stdout


    def opt_help_reactors(self):
        """
        Display a list of possibly available reactor names.
        """
        for r in reactors.getReactorTypes():
            self.messageOutput.write('    %-4s\t%s\n' %
                                     (r.shortName, r.description))
        raise SystemExit(0)


    def opt_reactor(self, shortName):
        """
        Which reactor to use (see --help-reactors for a list of possibilities)
        """
        # Actually actually actually install the reactor right at this very
        # moment, before any other code (for example, a sub-command plugin)
        # runs and accidentally imports and installs the default reactor.
        #
        # This could probably be improved somehow.
        try:
            installReactor(shortName)
        except NoSuchReactor:
            msg = ("The specified reactor does not exist: '%s'.\n"
                   "See the list of available reactors with "
                   "--help-reactors" % (shortName,))
            raise usage.UsageError(msg)
        except Exception, e:
            msg = ("The specified reactor cannot be used, failed with error: "
                   "%s.\nSee the list of available reactors with "
                   "--help-reactors" % (e,))
            raise usage.UsageError(msg)
    opt_r = opt_reactor




class ServerOptions(usage.Options, ReactorSelectionMixin):

    optFlags = [['savestats', None,
                 "save the Stats object rather than the text output of "
                 "the profiler."],
                ['no_save','o',   "do not save state on shutdown"],
                ['encrypted', 'e',
                 "The specified tap/aos/xml file is encrypted."],
                ['nothotshot', None,
                 "DEPRECATED. Don't use the 'hotshot' profiler even if "
                 "it's available."]]

    optParameters = [['logfile','l', None,
                      "log to a specified file, - for stdout"],
                     ['profile', 'p', None,
                      "Run in profile mode, dumping results to specified file"],
                     ['profiler', None, "hotshot",
                      "Name of the profiler to use, 'hotshot' or 'profile'."],
                     ['file','f','twistd.tap',
                      "read the given .tap file"],
                     ['python','y', None,
                      "read an application from within a Python file (implies -o)"],
                     ['xml', 'x', None,
                      "Read an application from a .tax file "
                      "(Marmalade format)."],
                     ['source', 's', None,
                      "Read an application from a .tas file (AOT format)."],
                     ['rundir','d','.',
                      'Change to a supplied directory before running'],
                     ['report-profile', None, None,
                      'E-mail address to use when reporting dynamic execution '
                      'profiler stats.  This should not be combined with '
                      'other profiling options.  This will only take effect '
                      'if the application to be run has an application '
                      'name.']]

    #zsh_altArgDescr = {"foo":"use this description for foo instead"}
    #zsh_multiUse = ["foo", "bar"]
    zsh_mutuallyExclusive = [("file", "python", "xml", "source")]
    zsh_actions = {"file":'_files -g "*.tap"',
                   "python":'_files -g "*.(tac|py)"',
                   "xml":'_files -g "*.tax"',
                   "source":'_files -g "*.tas"',
                   "rundir":"_dirs"}
    #zsh_actionDescr = {"logfile":"log file name", "random":"random seed"}

    def __init__(self, *a, **kw):
        self['debug'] = False
        usage.Options.__init__(self, *a, **kw)

    def opt_debug(self):
        """
        run the application in the Python Debugger (implies nodaemon),
        sending SIGUSR2 will drop into debugger
        """
        defer.setDebugging(True)
        failure.startDebugMode()
        self['debug'] = True
    opt_b = opt_debug


    def opt_spew(self):
        """Print an insanely verbose log of everything that happens.
        Useful when debugging freezes or locks in complex code."""
        sys.settrace(util.spewer)
        try:
            import threading
        except ImportError:
            return
        threading.settrace(util.spewer)


    def opt_report_profile(self, value):
        """
        DEPRECATED.

        Manage --report-profile option, which does nothing currently.
        """
        warnings.warn("--report-profile option is deprecated and a no-op "
                      "since Twisted 2.6.", category=DeprecationWarning)


    def parseOptions(self, options=None):
        if options is None:
            options = sys.argv[1:] or ["--help"]
        usage.Options.parseOptions(self, options)

    def postOptions(self):
        if self.subCommand or self['python']:
            self['no_save'] = True

    def subCommands(self):
        from twisted import plugin
        plugins = plugin.getPlugins(service.IServiceMaker)
        self.loadedPlugins = {}
        for plug in plugins:
            self.loadedPlugins[plug.tapname] = plug
            yield (plug.tapname, None, lambda: plug.options(), plug.description)
    subCommands = property(subCommands)



def run(runApp, ServerOptions):
    config = ServerOptions()
    try:
        config.parseOptions()
    except usage.error, ue:
        print config
        print "%s: %s" % (sys.argv[0], ue)
    else:
        runApp(config)


def initialLog():
    from twisted.internet import reactor
    log.msg("twistd %s (%s %s) starting up" % (copyright.version,
                                               sys.executable,
                                               runtime.shortPythonVersion()))
    log.msg('reactor class: %s' % reactor.__class__)


def convertStyle(filein, typein, passphrase, fileout, typeout, encrypt):
    application = service.loadApplication(filein, typein, passphrase)
    sob.IPersistable(application).setStyle(typeout)
    passphrase = getSavePassphrase(encrypt)
    if passphrase:
        fileout = None
    sob.IPersistable(application).save(filename=fileout, passphrase=passphrase)

def startApplication(application, save):
    from twisted.internet import reactor
    service.IService(application).startService()
    if save:
         p = sob.IPersistable(application)
         reactor.addSystemEventTrigger('after', 'shutdown', p.save, 'shutdown')
    reactor.addSystemEventTrigger('before', 'shutdown',
                                  service.IService(application).stopService)

def getLogFile(logfilename):
    """
    Build a log file from the full path.
    """
    import warnings
    warnings.warn(
        "app.getLogFile is deprecated. Use "
        "twisted.python.logfile.LogFile.fromFullPath instead",
        DeprecationWarning, stacklevel=2)

    return logfile.LogFile.fromFullPath(logfilename)

