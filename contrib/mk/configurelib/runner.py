#!/usr/bin/env python
# Copyright (c) 2007 ActiveState Software Ltd.

#TODO: docstirg
#TODO: not sure configure() belongs in here

#TODO: pare down this import list
import os
import sys
from os.path import basename, join, splitext, exists
from glob import glob
import logging
import traceback
import optparse
import types
import re
import stat
from pprint import pprint

import configurelib
from configurelib.common import *



#---- configure

def configure(config_vars, argv=sys.argv, default_config_file_path="config.py",
              project_name=None, doc=None, serialize_header_cb=None):
    usage = "usage: %prog [OPTIONS...]"
    version = "%prog "+configurelib.__version__
    description = doc or (project_name is None
                          and "Configure this project."
                          or "Configure "+project_name)
    optparser = optparse.OptionParser(
        prog="configure", usage=usage, version=version,
        description=description)

    # Add the core options.
    optparser.add_option("-v", "--verbose", action="callback",
        callback=lambda opt, o, v, p: log.setLevel(logging.DEBUG),
        help="more verbose output")
    optparser.add_option("-q", "--quiet", action="callback",
        callback=lambda opt, o, v, p: log.setLevel(logging.WARNING),
        help="quieter output")
    optparser.add_option("-f", dest="config_file_path",
        default=default_config_file_path,
        help="specify an alternate config file (default is '%s')"
             % default_config_file_path)
    optparser.add_option("-r", "--reconfigure", action="store_true",
        default=False,
        help="reconfigure with the same options")

    # Add options from all config vars.
    for config_var in config_vars:
        config_var.add_options(optparser)

    # Parse the command line args. If --reconfigure is used, redo it.
    configure_opts = argv[1:]
    options, args = optparser.parse_args(configure_opts)
    if options.reconfigure:
        # Load the existing 'config_file_path' and configure with
        # '_configure_opts' defined there.
        module = _module_from_path(options.config_file_path)
        configure_opts = module._configure_opts
        options, args = optparser.parse_args(configure_opts)
    if args:
        raise ConfigureError("extra trailing args: %r" % args)

    # If -p|--profile is used, handle that.
    config_var_from_name = dict((v.name, v) for v in config_vars)
    if hasattr(options, "profile"):
        # Need to determine "profile" config var (and its deps) to get the
        # arguments from the selected profile. (Cheat a little bit: we know
        # that Profile's only dep is "target_plat".)
        for name in ("target_plat", "profile"):
            _determine_config_var(config_var_from_name[name], options,
                                  config_var_from_name)
        profile = config_var_from_name["profile"]
        options, args = optparser.parse_args(profile.profile_args, options)
    if args:
        raise ConfigureError("extra trailing args: %r" % args)

    # Determine each config var (handling dependencies).
    for config_var in _gen_vars_deps_first(config_vars,
                                           config_var_from_name):
        _determine_config_var(config_var, options, config_var_from_name)
    
    # Serialize.
    # For now we just support serializing to a single .py file.
    log.debug("serializing to '%s'", options.config_file_path)
    fout = open(options.config_file_path, 'w')
    try:
        fout.write(_dedent("""\
            #!/usr/bin/env python
            # %s -- %s configuration
            # Generated by "%s". Modifications will be lost.
            # See "%s --help" for details.

            _configure_opts = %r

            """ % (basename(options.config_file_path),
                   project_name or "project",
                   basename(argv[0]),
                   basename(argv[0]),
                   configure_opts)
        ))

        if serialize_header_cb is not None:
            serialize_header_cb("Python", fout)
        for name, var in sorted(config_var_from_name.iteritems()):
            var.serialize("Python", fout)

        fout.write(_dedent("""

            #---- mainline
            # Usage:
            #   python %s SUBSTRING...
            # Prints the value of all config variables matching each substring.
            #
            def _main():
                import sys
                from pprint import pprint
                m = sys.modules[__name__]
                var_names = [k for k in m.__dict__.keys() if k != "_main"]
                for substring in sys.argv[1:]:
                    substring_lower = substring.lower()
                    for var_name in var_names:
                        if substring_lower in var_name.lower():
                            print "%%s: %%r" %% (var_name, getattr(m, var_name))
            if __name__ == "__main__":
                _main()
            """ % (basename(options.config_file_path), )
        ))
    finally:
        fout.close()
    if sys.platform != "win32":
        # Make it executable so can use the convience mainline easily.
        curr_mode = stat.S_IMODE(os.stat(options.config_file_path).st_mode)
        os.chmod(options.config_file_path, curr_mode | 0100)

    print "\nWrote configuration to `%s'." % options.config_file_path
    

#---- mainline

def main(config_vars, argv=sys.argv, default_config_file_path="config.py",
         project_name=None, doc=None, serialize_header_cb=None):
    """A main() function for a configure.py.

        "config_vars" is a list of configuration variables -- each one
            an instance of configurelib.ConfigVar.
        "argv" (optional, default sys.argv) is the command line args array.
        "default_config_file_path" (optional) is the path to which the
            configuration will be serialized. By default this is "config.py",
            but it is recommended that a project-specific name along the
            lines of "${projprefix}config.py" be specified. E.g. ActivePython
            might use "apyconfig.py", Komodo might use "koconfig.py". This
            can be overridden with the '-f' option.
        "project_name" (optional) is the name of the project that will use
            this configuration.
        "doc" (optional) is a description for help output for the
            configure.py script.
        "serialize_header_cb" (optional) is a callback for adding to the
            serialization stream. It is called as
            "serialize_header_cb(format, stream)".
    """
    try:
        setup_logging()
        retval = configure(config_vars, argv, default_config_file_path,
                           project_name, doc, serialize_header_cb)
    except KeyboardInterrupt:
        sys.exit(1)
    except SystemExit:
        raise
    except:
        #TODO: see diffs with doitlib/runner.py
        exc_info = sys.exc_info()
        if hasattr(exc_info[0], "__name__"):
            exc_class, exc, tb = exc_info
            tb_path, tb_lineno, tb_func = traceback.extract_tb(tb)[-1][:3]
            exc_str = str(exc_info[1])
            sep = ('\n' in exc_str and '\n' or ' ')
            try:
                from mklib.common import relpath
            except ImportError:
                def relpath(path):
                    return path
            log.error("%s%s(%s:%s in %s)", exc_str, sep,
                      relpath(tb_path), tb_lineno, tb_func)
        else:  # string exception
            log.error(exc_info[0])
        if log.isEnabledFor(logging.DEBUG):
            print
            traceback.print_exception(*exc_info)
        sys.exit(1)
    else:
        sys.exit(retval)


#---- internal support stuff

# Recipe: text_escape (0.1) in C:\trentm\tm\recipes\cookbook
def _escaped_text_from_text(text, escapes="eol"):
    r"""Return escaped version of text.

        "escapes" is either a mapping of chars in the source text to
            replacement text for each such char or one of a set of
            strings identifying a particular escape style:
                eol
                    replace EOL chars with '\r' and '\n', maintain the actual
                    EOLs though too
                whitespace
                    replace EOL chars as above, tabs with '\t' and spaces
                    with periods ('.')
                eol-one-line
                    replace EOL chars with '\r' and '\n'
                whitespace-one-line
                    replace EOL chars as above, tabs with '\t' and spaces
                    with periods ('.')
    """
    #TODO:
    # - Add 'c-string' style.
    # - Add _escaped_html_from_text() with a similar call sig.
    import re
    
    if isinstance(escapes, basestring):
        if escapes == "eol":
            escapes = {'\r\n': "\\r\\n\r\n", '\n': "\\n\n", '\r': "\\r\r"}
        elif escapes == "whitespace":
            escapes = {'\r\n': "\\r\\n\r\n", '\n': "\\n\n", '\r': "\\r\r",
                       '\t': "\\t", ' ': "."}
        elif escapes == "eol-one-line":
            escapes = {'\n': "\\n", '\r': "\\r"}
        elif escapes == "whitespace-one-line":
            escapes = {'\n': "\\n", '\r': "\\r", '\t': "\\t", ' ': '.'}
        else:
            raise ValueError("unknown text escape style: %r" % escapes)

    # Sort longer replacements first to allow, e.g. '\r\n' to beat '\r' and
    # '\n'.
    escapes_keys = escapes.keys()
    escapes_keys.sort(key=lambda a: len(a), reverse=True)
    def repl(match):
        val = escapes[match.group(0)]
        return val
    escaped = re.sub("(%s)" % '|'.join([re.escape(k) for k in escapes_keys]),
                     repl,
                     text)

    return escaped

def _one_line_summary_from_text(text, length=78,
        escapes={'\n':"\\n", '\r':"\\r", '\t':"\\t"}):
    r"""Summarize the given text with one line of the given length.
    
        "text" is the text to summarize
        "length" (default 78) is the max length for the summary
        "escapes" is a mapping of chars in the source text to
            replacement text for each such char. By default '\r', '\n'
            and '\t' are escaped with their '\'-escaped repr.
    """
    if len(text) > length:
        head = text[:length-3]
    else:
        head = text
    escaped = _escaped_text_from_text(head, escapes)
    if len(text) > length:
        summary = escaped[:length-3] + "..."
    else:
        summary = escaped
    return summary


# Recipe: dedent (0.1.2) in C:\trentm\tm\recipes\cookbook
def _dedentlines(lines, tabsize=8, skip_first_line=False):
    """_dedentlines(lines, tabsize=8, skip_first_line=False) -> dedented lines
    
        "lines" is a list of lines to dedent.
        "tabsize" is the tab width to use for indent width calculations.
        "skip_first_line" is a boolean indicating if the first line should
            be skipped for calculating the indent width and for dedenting.
            This is sometimes useful for docstrings and similar.
    
    Same as dedent() except operates on a sequence of lines. Note: the
    lines list is modified **in-place**.
    """
    DEBUG = False
    if DEBUG: 
        print "dedent: dedent(..., tabsize=%d, skip_first_line=%r)"\
              % (tabsize, skip_first_line)
    indents = []
    margin = None
    for i, line in enumerate(lines):
        if i == 0 and skip_first_line: continue
        indent = 0
        for ch in line:
            if ch == ' ':
                indent += 1
            elif ch == '\t':
                indent += tabsize - (indent % tabsize)
            elif ch in '\r\n':
                continue # skip all-whitespace lines
            else:
                break
        else:
            continue # skip all-whitespace lines
        if DEBUG: print "dedent: indent=%d: %r" % (indent, line)
        if margin is None:
            margin = indent
        else:
            margin = min(margin, indent)
    if DEBUG: print "dedent: margin=%r" % margin

    if margin is not None and margin > 0:
        for i, line in enumerate(lines):
            if i == 0 and skip_first_line: continue
            removed = 0
            for j, ch in enumerate(line):
                if ch == ' ':
                    removed += 1
                elif ch == '\t':
                    removed += tabsize - (removed % tabsize)
                elif ch in '\r\n':
                    if DEBUG: print "dedent: %r: EOL -> strip up to EOL" % line
                    lines[i] = lines[i][j:]
                    break
                else:
                    raise ValueError("unexpected non-whitespace char %r in "
                                     "line %r while removing %d-space margin"
                                     % (ch, line, margin))
                if DEBUG:
                    print "dedent: %r: %r -> removed %d/%d"\
                          % (line, ch, removed, margin)
                if removed == margin:
                    lines[i] = lines[i][j+1:]
                    break
                elif removed > margin:
                    lines[i] = ' '*(removed-margin) + lines[i][j+1:]
                    break
            else:
                if removed:
                    lines[i] = lines[i][removed:]
    return lines

def _dedent(text, tabsize=8, skip_first_line=False):
    """_dedent(text, tabsize=8, skip_first_line=False) -> dedented text

        "text" is the text to dedent.
        "tabsize" is the tab width to use for indent width calculations.
        "skip_first_line" is a boolean indicating if the first line should
            be skipped for calculating the indent width and for dedenting.
            This is sometimes useful for docstrings and similar.
    
    textwrap.dedent(s), but don't expand tabs to spaces
    """
    lines = text.splitlines(1)
    _dedentlines(lines, tabsize=tabsize, skip_first_line=skip_first_line)
    return ''.join(lines)


def _gen_var_deps_and_var(var, config_var_from_name, already_yielded_names):
    if var.name not in already_yielded_names:
        # Handle each dependency of this var.
        if isinstance(var.deps, types.GeneratorType):
            dep_names = var.deps()
        else:
            dep_names = var.deps
        if dep_names is not None:
            for dep_name in dep_names:
                if dep_name not in config_var_from_name:
                    raise ConfigureError("no config var named '%s' "
                                         "(is a dependency of '%s')"
                                         % (dep_name, var.name))
                log.debug("config var '%s' depends on '%s'",
                          var.name, dep_name)
                for dep_var in _gen_var_deps_and_var(
                        config_var_from_name[dep_name],
                        config_var_from_name,
                        already_yielded_names):
                    yield dep_var

        already_yielded_names.add(var.name)
        yield var

def _gen_vars_deps_first(config_vars, config_var_from_name):
    """Generate the given config vars, ensuring dependencies are yielded
    first.
    """
    already_yielded_names = set()
    for config_var in config_vars:
        for var in _gen_var_deps_and_var(config_var, config_var_from_name,
                                         already_yielded_names):
            yield var

# Recipe: module_from_path (1.0+) in C:\trentm\tm\recipes\cookbook
def _module_from_path(path):
    import imp, os
    dir = os.path.dirname(path) or os.curdir
    name = os.path.splitext(os.path.basename(path))[0]
    iinfo = imp.find_module(name, [dir])
    return imp.load_module(name, *iinfo)


def _determine_config_var(config_var, options, config_var_from_name):
    """Determine the given config var.
    
    If the config var has already been determined, this just returns.
    """
    if config_var.determined:
        return

    log.debug("determine config var '%s'", config_var.name)
    prefix = "%s: " % config_var.name
    sys.stdout.write(prefix)
    sys.stdout.flush()
    try:
        config_var.determine(config_var_from_name, options)
        config_var.determined = True
    except Exception:
        sys.stdout.write("(error)\n\n")
        raise
    else:
        if config_var.value is None:
            sys.stdout.write("(none)\n")
        else:
            WIDTH = 79 # get the actual width
            suffix = _one_line_summary_from_text(
                str(config_var.value), length=(WIDTH-len(prefix)))
            sys.stdout.write(suffix + '\n')



# Recipe: pretty_logging (0.1.1) in C:\trentm\tm\recipes\cookbook
class _PerLevelFormatter(logging.Formatter):
    """Allow multiple format string -- depending on the log level.
    
    A "fmtFromLevel" optional arg is added to the constructor. It can be
    a dictionary mapping a log record level to a format string. The
    usual "fmt" argument acts as the default.
    """
    def __init__(self, fmt=None, datefmt=None, fmtFromLevel=None):
        logging.Formatter.__init__(self, fmt, datefmt)
        if fmtFromLevel is None:
            self.fmtFromLevel = {}
        else:
            self.fmtFromLevel = fmtFromLevel
    def format(self, record):
        record.levelname = record.levelname.lower()
        if record.levelno in self.fmtFromLevel:
            #XXX This is a non-threadsafe HACK. Really the base Formatter
            #    class should provide a hook accessor for the _fmt
            #    attribute. *Could* add a lock guard here (overkill?).
            _saved_fmt = self._fmt
            self._fmt = self.fmtFromLevel[record.levelno]
            try:
                return logging.Formatter.format(self, record)
            finally:
                self._fmt = _saved_fmt
        else:
            return logging.Formatter.format(self, record)

def setup_logging(stream=None):
    """Do logging setup:

    We want a prettier default format:
         do: level: ...
    Spacing. Lower case. Skip " level:" if INFO-level. 
    """
    hdlr = logging.StreamHandler(stream)
    defaultFmt = "%(name)s: %(levelname)s: %(message)s"
    infoFmt = "%(name)s: %(message)s"
    fmtr = _PerLevelFormatter(fmt=defaultFmt,
                              fmtFromLevel={logging.INFO: infoFmt})
    hdlr.setFormatter(fmtr)
    logging.root.addHandler(hdlr)
    log.setLevel(logging.INFO)

