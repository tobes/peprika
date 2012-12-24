import cStringIO
import sys
import os
import ast
from keyword import iskeyword
import re
import tokenize
import difflib
import textwrap

from colorama import Fore, Back
import pep8

INDENT_SIZE = 4
MAX_LINE_LEN = 79
NEWLINE = '\n'

BASIC_TOKENS = [tokenize.NAME, tokenize.STRING, tokenize.NUMBER]


class Peprika(object):

    opposites = {
        '(': ')',
        '[': ']',
        '{': '}',
        '"': "'",
        "'": '"',
    }

    def __init__(self, options):
        self.options = options
        self.added = 0
        self.deleted = 0
        self.errors = 0
        self.files = 0

        self.pep_examples = {}

    def _NEWLINE(self):
        self.continuation = False
        self.l_line = None
        self.nl = True

    def _DEDENT(self):
        if not self.block_indent:
            self.indent_level -= 1
            self.last_indent -= INDENT_SIZE
        else:
            self.block_indent += 1
        return False

    def _INDENT(self):
        if not self.block_indent:
            self.indent_level += 1
            self.last_indent += INDENT_SIZE
        else:
            self.block_indent -= 1
        return False

    def _NL(self):
        self.l_line = None
        self.nl = True

    def _STRING(self):
        # Change quotes to single or double if requested.
        if self.options.fix_quotes:
            t = self.t_value
            q = self.options.fix_quotes
            qq = self.opposites[self.options.fix_quotes]
            sp = u''
            if t.startswith('u'):
                sp += t[0]
                t = t[1:]
            if t.startswith('r'):
                sp += t[0]
                t = t[1:]
            if t.startswith(qq):
                s = 1
                if t[1:3] == qq * 2:
                    s = 3
                t = t[s:-s]
                # Only change if the string contains no ' or " chars
                if '"' not in t and "'" not in t:
                    self.t_value = sp + (q * s) + t + (q * s)
        # docstrings
        if self.stream_offset(-1)[0] == tokenize.INDENT:
            pass
            # sort white space
            # FIXME docstring changes break ast
          #  lines = self.t_value.splitlines()
           # self.t_value = NEWLINE.join([line.rstrip() for line in lines])

    def _NAME(self):
        self.add_blanklines_if_needed()

        # Keep keywords spaced
        if iskeyword(self.t_value):
            self.need_space_after = True
            if (not (self.l_value == '('
                     or (self.in_container() and self.l_value == '='))):
                self.need_space_before = True
        # Keep space between variables
        if self.l_type == tokenize.NAME:
            self.need_space_before = True

    def _OP(self):
        # commas should be on the end of lines not at the start
        if self.t_value == ',' and not self.line:
            last_line = self.out[-1].rstrip(NEWLINE) + ',' + NEWLINE
            self.out = self.out[:-1] + [last_line]
            self.t_type = tokenize.NL
            return False

        # fix depreciation
        if self.t_value == '<>':
            self.t_value = '!='

        # General settings
        if self.t_value not in '(){}[].':
            self.need_space_before = True
            self.need_space_after = True

        # if there is a space before .;: operators remove it.
        if self.t_value in ':,;':
            if self.line and self.line[-1] == ' ':
                self.line = self.line[:-1]
            self.need_space_before = False

        if self.t_value in ')}]':
            if self.line and self.line[-1] == ' ':
                if len(self.line) > 1 and self.line[-2] != ',':
                    self.line = self.line[:-1]

        # No space between assignments/defaults for keywords
        if self.t_value == '=' and self.in_container():
            self.need_space_before = False
            self.need_space_after = False

        # Special case for (*args, **kw)
        if self.t_value in ['*', '**'] and self.in_container():
            if self.l_value in ['(', ',', '\n']:
                self.need_space_after = False
            if self.l_value == '(':
                self.need_space_before = False

        # Special case for list slices
        if (self.t_value == ':' and self.in_container()
            and self.last_container()['char'] == '['):
            self.need_space_after = False

        # decorators
        if self.t_value == '@':
            self.add_blanklines_if_needed()
            self.need_space_after = False

        # differentiate between subtraction and negation
        if (self.t_value in '-'
                and not (self.l_type in BASIC_TOKENS
                         or (self.l_type == tokenize.OP
                             and self.l_value in ')}]'))):
            self.need_space_before = False
            self.need_space_after = False

    def _COMMENT(self):
        # Format in-line comments if wanted
        if self.line:
            self.need_space_before = True
            self.t_value = ' ' + re.sub('^#+([^ ])', '# \\1', self.t_value)
            if (self.options.reflow_inline_comments
                    and len(''.join(self.line).rstrip()) + 1
                    + len(self.t_value.rstrip()) > MAX_LINE_LEN):
                self.out.append(
                    self.format_comment(self.t_value.lstrip(),
                                        remove_initial_indent=False)
                    + NEWLINE
                )
                self.t_value = ''
                self.need_space_before = False

        # Comments directly before or after indentation
        # changes do not see them but should be aligned to
        # them so look forward to see if they exists and
        # prematurely alter the indentation level.  We only do
        # this once for multi-line comments not for each
        # comment.
        else:
            if not self.block_indent:
                # scan forward to find indents/dedents as they follow the
                # comment in the stream
                self.scan_indent()
                if self.options.reflow_comments:
                    self.t_value = self.format_comment(self.t_value)

    def stream_offset(self, offset=0):
        ''' Get the stream item relative to the one currently being
        processed. '''
        try:
            return self.stream[self.offset + offset]
        except IndexError:
            return (None, '', False)

    def add_blanklines_if_needed(self):
        ''' Add extra blank lines before class and def statements.  Two if
        on top level otherwise one. '''
        if (self.options.add_blank_lines
                and self.out
                and not self.out[-1].lstrip().startswith('@')
                and not self.line and self.t_value in ['@', 'class', 'def']):
            if not self.blanks:
                self.out.append(NEWLINE)
            if not self.indent_level:
                self.out.append(NEWLINE)

    def scan_indent(self, update=True, start=1, break_on_nl=True):
        ''' Scan down the token stream looking for indents/dedents we keep
        looking to the first none blank/whitespace or comment line. '''
        c = start
        indent = 0
        nl_count = 0
        while True:
            toktype2 = self.stream_offset(c)[0]
            # We only indent one level at a time
            if toktype2 == tokenize.INDENT:
                if update:
                    self.indent_level += 1
                    self.block_indent += 1
                indent += 1
                break
            # Dedenting can happen multiple times
            elif toktype2 == tokenize.DEDENT:
                if update:
                    self.indent_level -= 1
                    self.block_indent -= 1
                indent -= 1
                c += 1
            # For multi-line comments look after the last comment
            elif toktype2 == tokenize.NL:
                nl_count += 1
                if break_on_nl and nl_count == 2:
                    break
                c += 1
            elif toktype2 == tokenize.COMMENT:
                nl_count = 0
                c += 1
                pass
            else:
                break
        return indent

    def format_comment(self, comment, remove_initial_indent=True):
        ''' Reformat the comment to fit on line length. '''
        # don't reformat hashbangs
        if not self.out and not self.line and comment.startswith('#!'):
            return comment
        m = re.match('#+', comment)
        indent = 0

        # allow long comments
        comment = comment[len(m.group(0)):].strip()
        if len(comment) + indent < MAX_LINE_LEN:
            return m.group(0) + ' ' + comment
        prefix = (' ' * indent) + m.group(0) + ' '
        if not comment:
            return '#'
        comments = textwrap.wrap(comment, initial_indent=prefix,
                                 subsequent_indent=prefix)
        for l in comments[:-1]:
            self.out.append(l + NEWLINE)
        if remove_initial_indent:
            return comments[-1][indent:]
        else:
            return comments[-1]


    def output_line(self, no_blank=False):
        ''' Append a created line to our list '''

        indent_last = self.indent_last
        indent_current = self.indent_current
        if indent_last:
            c_indent = self.indents_last[indent_last - 1]
            indent = c_indent['opening']
            if indent is None:
               indent = c_indent['indent']
            c_indent['opening'] = None
        else:
            indent = self.indent_level * INDENT_SIZE
            c_indent = None

        if indent_current < indent_last:
            c_indent = self.indents_last[indent_last - 1]
            indent = c_indent['closing']
        if self.continuation_last:
            indent += INDENT_SIZE
            if self.indents():
                indent += INDENT_SIZE


        self.last_indent = indent

        self.continuation_last = self.continuation
        text = ''.join(self.line).rstrip()
        if text:
            # have actual content
            t = text.splitlines(1)
            self.out.append((' ' * indent) + t[0].rstrip(NEWLINE) + NEWLINE)
            # We have to do some slightly crazy stuff after strings as they
            # claim to have nl following but don't always want one for example
            # with following ,
            next_is_op = self.stream_offset(0)[0] == tokenize.OP
            for i in range(1, len(t)):
                if not next_is_op or t[i] != t[i].rstrip(NEWLINE):
                    self.out.append(t[i].rstrip(NEWLINE) + NEWLINE)
                else:
                    return t[i]

            self.blanks = 0
        else:
            # blank/whitespace lines
            if self.out and self.out[-1].lstrip().startswith('@'):
                # don't allow a blank line after a decorator
                return

            if not self.blanks and not no_blank:
                if self.options.pad_blank_lines:
                    # add a space padded blank line
                    indent = (
                        INDENT_SIZE * (
                            self.indent_level - self.block_indent
                            + self.scan_indent(update=False, start=0,
                                               break_on_nl=False)
                        )
                    )
                    self.out.append((' ' * indent) + NEWLINE)
                if self.options.keep_whitespace:
                    self.out.append(self.t_line[:-1] + NEWLINE)
                else:
                    self.out.append(NEWLINE)
                if self.options.kill_blank_lines:
                    self.blanks = 1

    def do_newline(self):
        remainder = self.output_line()
        self.hanging = self.hanging_current
        self.hindent_old = self.hindent

        # Add our line
        # Update our hanging indent

        self.hanging_old = self.hanging
        self.hindent = self.hindent[:self.hanging  + 1]
        self.clear_container_count = 0
        self.nl = False
        self.line = []
        self.remainder = False
        if remainder:
            self.line = [remainder]
            self.remainder = True
        self.on_newline()

    def token_name(self, t):
        ''' Return the name of the token. '''
        return tokenize.tok_name.get(t, t)

    def reformat(self, source):
        ''' The main beast '''
        s = cStringIO.StringIO(''.join(source))
        tokgen = tokenize.generate_tokens(s.readline)

        # Create our stream to allow looking ahead of our current line
        self.stream = []
        for t_type, t_value, start, end, t_line in tokgen:
            if 0:  # Change to if 1 to see the tokens fly by.
                print ('%10s %-20r' %
                       (
                           self.token_name(t_type),
                           t_value
                       )
                       ), t_line[:-1]
            self.stream.append((t_type, t_value, t_line, start, end))

        self.out = []  # Final output
        self.line = []  # elements for the current line being built
        self.l_type = None
        self.l_value = None
        self.l_line = None
        self.indent_level = 0  # Current level of indentation
        # Prevent indentation level changes when they have been
        # already processed for comments
        self.block_indent = 0
        self.container = []  # Record of container depth for ({[
        self.clear_container_count = 0
        self.hindent = [{'pos':0, 'indent':0, 'open':True}]  # Indentation amounts for hanging elements
        self.hindent_old = [{'pos':0, 'indent':0, 'open':True}]  # Indentation cache
        self.hanging = 0  # Current hanging level
        self.hanging_old = 0  # Current hanging level
        self.hanging_current = 0
        self.blanks = 0  # number of blank lines at the output end
        self.nl = False  # Boolean set to request a line to be output
        self.continuation = False
        self.continuation_last = False
        self.remainder = False
        self.last_indent = 0
        self.last_closed_paren = None

        self.init()

        # Process the stream
        self.offset = 0
        while self.offset < len(self.stream):
            self.t_type, self.t_value, self.t_line, self.t_start, self.t_end = self.stream[self.offset]
            self.need_space_before = False
            self.need_space_after = False

            name = tokenize.tok_name.get(self.t_type, self.t_type)
            try:
                if getattr(self, '_' + name)() is False:
                    self.offset += 1
                    self.l_type = self.t_type
                    continue
            except AttributeError:
                pass

            # Continuation lines
            if not self.nl and self.l_line and self.l_line_no != self.t_start[0]:
                # only use backslash if not in a bracket etc.
                # Also multi-line strings need to be accounted for
                n_token = self.stream_offset(0)
                if n_token[0] != tokenize.STRING:# or '\n' not in n_token[1]:
                    if not self.in_container():
                        if (self.line and self.line[-1] != ' '):
                            self.line.append(' ')
                        self.line.append('\\')
                        self.continuation = True
                    else:
                        self._NEWLINE()
                    self.do_newline()

            # add the token to the line with any request whitespace
            if self.t_type not in [tokenize.NEWLINE, tokenize.NL]:
                if (self.line and self.need_space_before
                        and self.line[-1] != ' '):
                    self.line.append(' ')
                self.line.append(self.t_value)
                if self.need_space_after:
                    self.line.append(' ')

            # Hanging indents need recording of where container elements start
            if self.t_value and self.t_value in '({[':
                self.hanging_current += 1
                self.indent_in()
            else:
                self.clear_container_count = 0

            if self.t_value and self.t_value in ')}]':
                self.indent_out()
                self.hanging_current -= 1
                self.container = self.container[:-1]

            # Newline requested
            if self.nl:
                self.continuation = False
                self.do_newline()
            else:
                self.l_line = self.t_line

            self.l_type = self.t_type
            self.l_value = self.t_value
            self.l_line_no = self.t_end[0]
            self.offset += 1

        # generate the last line
        self.output_line(no_blank=True)

        # remove any trailing blank line
        if (self.options.kill_blank_lines
                and self.out and not self.out[-1].strip()):
            self.out = self.out[:-1]
        return self.out

    def init(self):
        self.indents_current = []
        self.indents_last = []
        self.indent_current = 0
        self.indent_last = 0

    def on_newline(self):
        self.indents_last = self.indents_current[:]
        self.indent_last = self.indent_current
        if not self.indent_current:
            self.last_indent = self.indent_level * INDENT_SIZE

    def indent_in(self):
        char = self.t_value
        level = len(self.indents_current) + 1
        line_len = len(''.join(self.line).rstrip())

        if self.indents_current:
            last = self.indents_current[-1]
            last_hanging = last['hanging']
        else:
            last = None
            last_hanging = None

        indent = self.last_indent
        line = self.t_start[0]
        hanging = ((self.stream_offset(1)[0] == tokenize.NL or (self.stream_offset(1)[0] != tokenize.STRING and self.stream_offset(1)[3][0] != self.l_line_no)))
        if self.continuation_last and level == 1 and char in '[{' and self.stream_offset(1)[0] != tokenize.NL :
            hanging = False
        closing_op_starts_line = self.closing_op_starts_line()
        indents = self.indents()
        closable = False
        if indents:
            indent += INDENT_SIZE
        if hanging:
            if self.continuation_last:
                indent += INDENT_SIZE
            opening = None
            minimum = indent
            if last and last['line'] != line:
                if level > 2 and self.stream_offset(-2)[1] == ',' and last['char'] in '{[':
                    prev = self.previous_line_paren()
                    if not (prev and prev['char'] in '{[' and prev['hanging'] and not prev['closable']):
                        indent = last['indent'] + INDENT_SIZE
                else:
                    indent = last['indent']
                opening = indent
                closing = indent
                if last_hanging:
                    pass
                else:
                    pass
                if line_len > 1:
                    opening = None
                    last['closable'] = False
            else:
                if level > 1 and self.previous_line_paren():
                    indent = self.previous_line_paren()['indent']
                closing = indent

            if not closing_op_starts_line and not self.closing_op_on_same_line():
                if char == '(' and level == 1 and self.last_closed_paren and self.last_closed_paren['line'] != line and self.stream_offset(-1)[1] == ',':
                    pass
                    indent += INDENT_SIZE
                    closing = indent + INDENT_SIZE
                else:
                    pass
                    closing = indent + INDENT_SIZE

            if self.closing_op_starts_line() and self.stream_offset(self.find_closing_op_offset())[1] == ':':
                pass
                closing = self.last_indent
            else:
                indent +=  INDENT_SIZE
            if last and indent == last['indent']:
                pass
                closing = indent
        else:
            # not hanging
            indent = self.last_indent
            if last and last['line'] == line and last['indent'] and last['line_len'] > 1:
                indent = last['indent'] - last['line_len']
                pass
            if self.continuation_last:
                pass
                indent += INDENT_SIZE
            if last and last['line'] != line:
                pass
                indent = last['indent']
            if line_len > 1:
                indent += line_len
            if indents and indent == self.last_indent + INDENT_SIZE:
                if not self.line_has_another_opener():
                    indent += INDENT_SIZE
            if level == 1 and self.last_closed_paren and self.last_closed_paren['line_close'] == line and self.last_closed_paren['line'] != line:
                indent = self.last_closed_paren['indent'] + line_len
            if last and last['line'] == line and self.stream_offset(-1)[1] in '{[':
                pass
                indent = last['indent'] + 1
            opening = None
            closing = indent
            minimum = indent
            if closing_op_starts_line and line_len == 1:
                opening = indent
                indent += 1
                closing = indent
            elif line_len == 1 and last and last['line'] == line - 1 and not self.closing_op_on_same_line():
                opening = indent
                indent += 1
                closing = indent
            elif line_len == 1 and last and last['line'] < line and not self.closing_op_on_same_line() and char == '(':
                opening = indent
                indent += 1
                closing = indent
            elif last_hanging and line_len == 1 and char in '{[' and last and last['char'] in '{[' and not self.closing_op_on_same_line():
                opening = indent
                indent += 1
                closing = indent
            elif last and last['char'] == '(' and last['hanging'] and char in '{[' and line_len == 1 and not self.closing_op_on_same_line():
                opening = indent
                indent += 1
                closing = indent

            if closing_op_starts_line and level == 1 and char == '(' and self.stream_offset(self.find_closing_op_offset())[1] == '\n':
                pass
                closable = True
            if self.stream_offset(1)[1] in '({[':
                if self.stream_offset(self.find_closing_op_offset(1))[1] not in ')}]':
                    pass
                    closable = True
            elif last:
                pass
                last['closable'] = False
                if last['indent'] > indent and last['line'] == line and last['next_char'] in '({[':
                    pass
                    last['indent'] = indent - 1
            if level == 1:
                pass

        info = dict(closing=closing,
                    opening=opening,
                    hanging=hanging,
                    indent=indent,
                    last_indent=self.last_indent,
                    line = line,
                    line_close = self.stream_offset(self.find_closing_op_offset() - 1)[3][0],
                    line_len = line_len,
                    level=level,
                    minimum=minimum,
                    closable=closable,
                    next_char=self.stream_offset(1)[1],
                    char=char)
        self.indents_current.append(info)
        self.indent_current += 1
        if self.stream_offset(-1)[0] == tokenize.NL:
            self.on_newline()

    def indent_out(self):
        last = self.indents_current.pop()
        self.indent_current -= 1
        self.last_closed_paren = last
        if last['level'] > 1:
            current = self.indents_current[-1]
            if current['line_close'] == last['line_close']:
                return
            if not current['closable']:
                if last['closing'] < current['closing'] and not current['hanging']:
                    pass
                    current['closing'] = last['closing']
                return
            if last['hanging'] and current['next_char'] in '({[':
                pass
                current['closing'] = last['last_indent']
                current['minimum'] = last['last_indent']
                current['indent'] = last['last_indent']
                current['hanging'] = True
            elif  current['level'] == 1 and current['char'] == '(' and current['closable'] and (last['hanging'] or last['char'] == '('):
                pass
                current['closing'] = current['last_indent']
                current['minimum'] = last['last_indent']
                current['indent'] = last['last_indent']
                current['hanging'] = True

    def in_container(self):
        return bool(self.indent_current)

    def last_container(self):
        return self.indents_current[-1]

    def previous_line_paren(self):
        line = self.t_start[0]
        for i in xrange(self.indent_current, 0, -1):
            if self.indents_current[i -1]['line'] != line:
                return self.indents_current[i -1]
        return None

    def closing_op_starts_line(self):
        c = self.find_closing_op_offset()
        if self.stream_offset(c - 2)[0] == tokenize.NL:
            return True
        return False

    def next_line_starts_with(self):
        c = 1
        line = self.stream_offset(c)[3][0]
        while self.stream_offset(c)[3][0] == line:
            c += 1
        return self.stream_offset(c + 1)

    def closing_op_line_closing_op(self):
        c = self.find_closing_op_offset()
        cons = []
        con_min = 0
        line = self.stream_offset(c)[3][0]
        while self.stream_offset(c)[3][0] == line:
            t_next = self.stream_offset(c)
            if t_next[1] in '({[':
                cons.append(t_next[1])
            if t_next[1] in ')}]':
                if cons:
                    cons = cons[:-1]
                else:
                    con_min -= 1
            c += 1
        if con_min < 0:
            return self.indents_current[con_min]
        else:
            return None

    def closing_op_on_same_line(self):
        cons = []
        c = 1
        line = self.stream_offset(c)[3][0]
        while self.stream_offset(c)[3][0] == line:
            t_next = self.stream_offset(c)
            if t_next[1] in '({[':
                cons.append(t_next[1])
            if t_next[1] in ')}]':
                if cons:
                    cons = cons[:-1]
                else:
                    return True
            c += 1
        return False


    def find_closing_op_offset(self, c=0):
        cons = [self.stream_offset(c)[1]]
        c += 1
        while cons:
            t_next = self.stream_offset(c)
            if t_next[1] in '({[':
                cons.append(t_next[1])
            if t_next[1] in ')}]':
                cons = cons[:-1]
            c += 1
        return c


    def line_has_another_opener(self):
        cons = []
        c = 1
        line = self.stream_offset(c)[3][0]
        while self.stream_offset(c)[3][0] == line:
            t_next = self.stream_offset(c)
            if t_next[1] in '({[':
                cons.append(t_next[1])
            if t_next[1] in ')}]':
                cons = cons[:-1]
            if t_next[0] == tokenize.NEWLINE:
                break
            c += 1
        return bool(len(cons))


    def indents(self):
        c = - 1
        while True:
            t_next = self.stream_offset(c)
            if t_next[0] == tokenize.NEWLINE:
                return self.stream_offset(c - 1)[1] == ':'
            c += 1


    def out_diff(self, filename, origional, current):
        ''' Print out a diff between the original and generated code '''
        col = self.options.color_diff
        if self.options.show_diff:
            if col:
                print Fore.BLUE + filename
                print ('=' * len(filename)) + Fore.RESET
            else:
                print filename + '\n' + ('=' * len(filename))

        count = 0
        for line in difflib.unified_diff(origional, current):
            count += 1
            if count <= 2:
                line = line.rstrip() + '\n'
            if line[0] == '-':
                self.deleted += 1
                start = Fore.RED
            elif line[0] == '+':
                self.added += 1
                start = Fore.GREEN
            else:
                start = ''
            if self.options.show_diff:
                if col and start:
                    ln = start + line.rstrip() + Fore.RESET
                    ln += Back.RED + line[len(line.rstrip()):-1] + Back.RESET
                    print ln
                else:
                    print line[:-1]

    def process_file(self, filename):
        # print '# %s' % filename
        f = open(filename, 'r')
        data = []
        for line in f:
            data.append(line)
        try:
            moo2 = ast.parse(''.join(data))
        except Exception as e:
            err = ('File %s has errors and was not be processed: %s'
                   % (filename, e.msg))
            print >> sys.stderr, err
            self.errors += 1
            return

        if self.options.pep8:
            pre_pep_errors = self.find_pep8_errors(filename=filename)
        data_copy = data[:]
        data = self.reformat(data)
        moo = ast.parse(''.join(data_copy))

        try:
            moo2 = ast.parse(''.join(data))
        except Exception:
            self.explode(filename)

        if not ast.dump(moo) == ast.dump(moo2):
            self.explode(filename)

        if self.options.pep8:
            post_pep_errors = self.find_pep8_errors(filename=filename,
                                                    lines=data)
            self.output_pep8_errors(filename, pre_pep_errors, post_pep_errors,
                                    full=True)

        if self.options.show_diff or self.options.stats:
            self.out_diff(filename, data_copy, data)
        if self.options.output_file:
            print ''.join(data),
        f = open('poo.py', 'w')
        f.write(''.join(data))
        f.close()

        self.files += 1

    def process_directory(self, directory):
        for (dirpath, dirnames, filenames) in os.walk(directory):
            # this is crazy
            # so is this
            for name in filenames:
                if name.endswith('.py'):
                    self.process_file(os.path.join(dirpath, name))

    def explode(self, filename):
        msg = ['\nPeprika Error:\n\nSomething has gone very wrong formatting '
               'this code.  The file being reformatted is \n%s\nAborting '
               'please report this issue preferably including the code that '
               'caused this error.\n']
        print msg
#        sys.exit(msg[0] % filename)


    def find_pep8_errors(self, filename=None, lines=None):


        try:
            sys.stdout = cStringIO.StringIO()
            checker = pep8.Checker(filename=filename, lines=lines)
            checker.check_all()
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = sys.__stdout__

        file_errors = {}
        errors = 0
        for line in output.split('\n'):
            parts = line.split(' ', 2)
            if len(parts) == 3:
                errors += 1
                location, error, desc = parts
                if error not in file_errors:
                    file_errors[error] = {'desc': desc, 'locations': []}
                    if lines and error not in self.pep_examples:
                        self.pep_examples[error] = '%s %s' % (filename, desc)
                file_errors[error]['locations'].append(location)
        return {'errors': file_errors, 'count': errors}

    def output_pep8_errors(self, filename, pre, post, full=False):
        pre_errors = pre['count']
        post_errors = post['count']
        fixed = pre_errors - post_errors
        if post_errors:
            print '%s pep8 errors remaining %s (%s fixed)' % (filename,
                                                              post_errors,
                                                              fixed)
        else:
            pass
           # print '%s Perfect no errors (%s fixed)' % (filename, fixed)
        if full:
            errors = post['errors']
            for error in errors:
                info = errors[error]
                print '%s (%s) %s' % (error, len(info['locations']),
                                      info['desc'])


class Options(object):
    kill_blank_lines = True
    add_blank_lines = True
    pad_blank_lines = False
    show_diff = False
    color_diff = True
    output_file = False
    align_indents = False
    reflow_comments = True
    reflow_inline_comments = True
    fix_quotes = None
    keep_whitespace = False
    stats = False
    pep8 = True

'''
keep whitespace on blank lines
just kill/sort whitespace
don't move comments
newline types \n \r \n\r auto
cleanup levels
format docstrings

'''


def main():
    options = Options()
    peprika = Peprika(options)

    import argparse
    parser = argparse.ArgumentParser(description='Code reformatter for python')
    parser.add_argument('-d', '--diff', action='store_true')
    parser.add_argument('-c', '--color', action='store_true')
    parser.add_argument('-o', '--output', action='store_true')
    parser.add_argument('-r', '--reflow', action='store_true')
    parser.add_argument('-p', '--pad-blanklines', action='store_true')
    parser.add_argument('-s', '--supress-new-blanklines', action='store_true')
    parser.add_argument('-k', '--keep-blanklines', action='store_true')
    parser.add_argument('files', nargs=argparse.REMAINDER)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-q', '--single-quote', action='store_true')
    group.add_argument('-Q', '--double-quote', action='store_true')
    args = parser.parse_args()

    options.show_diff = args.diff
    options.color_diff = args.color
    options.output_file = args.output
    options.kill_blank_lines = not args.keep_blanklines
    options.reflow_comments = args.reflow
    options.reflow_inline_comments = args.reflow
    options.pad_blank_lines = args.pad_blanklines
    options.add_blank_lines = not args.supress_new_blanklines
    if args.single_quote:
        options.fix_quotes = "'"
    if args.double_quote:
        options.fix_quotes = '"'

    for filename in args.files:
        if os.path.isdir(filename):
            peprika.process_directory(filename)
        else:
            peprika.process_file(filename)

    if options.stats:
        print 'processed %s files' % peprika.files
        print '%s errors' % peprika.errors
        print '%s lines added' % peprika.added
        print '%s lines deleted' % peprika.deleted

    print '-' * 30
    for key, item in peprika.pep_examples.iteritems():
        print key, item

if __name__ == '__main__':
    main()

'''

w291 - breaks ast
e203 - need to move comma up a line
'''
