#!/usr/bin/python

"""Parsing and processing the HTW schedule"""

import urllib
import httplib
from BeautifulSoup import BeautifulSoup, BeautifulStoneSoup
from subprocess import Popen, PIPE
from tempfile import NamedTemporaryFile
from multiprocessing import Pool
from multiprocessing import pool
import argparse
import time
import sys


def get_color(idx):
    """Get fifferent colors for different numbers.

    This is used to color the html output.
    """
    colors = ["red", "blue", "green", "yellow", "orange", "gray"]
    return colors[idx % len(colors)]


def get_content(year, course, group):
    """Request the HTML content from htw server.

    This builds a HTTP-POST-request, connect to the HTW webserver, sends
    the request and reads the response.

    The parameters are the course number, the last two digits of the
    matriculation, and the study group number.

    On error None will be returned.
    """
    params = {'imm': year,
              'stuga': course,
              'grup': group,
              'lang': 1,
              'aktkw': 1,
              'pressme': 'S T A R T',
              'matr': '',
              'unix': '',
              'passi': ''}

    headers = {"Content-type": "application/x-www-form-urlencoded",
               "Accept": ("application/xml,"
                          "application/xhtml+xml,"
                          "text/html;q=0.9,"
                          "text/plain;q=0.8,"
                          "image/png,*/*;q=0.5")}
    conn = httplib.HTTPConnection("www2.htw-dresden.de:80")
    conn.request("POST",
                 "/~rawa/cgi-bin/auf/raiplan.php",
                 urllib.urlencode(params),
                 headers)

    response = conn.getresponse()
    if response.status != 200:
        conn.close()
        return None

    data = response.read()
    conn.close()
    return data


def extract_soups(page):
    """Extract the tables and the headline as BeautifulSoup objects.

    This takes the html-string of _one_ request, converts it to a
    BeautifulSoup instance and search for the headline and the four
    tables.

    The results will be returned in a dict with a list of the tables and
    the headline.
    """
    soup = BeautifulSoup(page)
    body = soup.html.body
    headline = body.findAll('h2')[0]
    table = []
    for week in range(0, 4):
        table.append(body.findAll('table')[2 + (week*2)])
    return {'headline': headline,
            'table': table}


def make_page(soaps_list):
    """Generate a HTML table with only the first unmodified tables.

    Parameter is a list of extract_soups outputs. Uses only for testing
    purposes.
    """
    page = """
    <html>
      <head>
        <title>Custom Schedule</title>
        <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
      </head>
      <body>
        %s
      </body>
    </html>
    """

    stuff = ''
    for soaps in soaps_list:
        stuff = (stuff +
                 soaps['headline'].encode('utf-8') +
                 soaps['table'][0].encode('utf-8'))
    return page % stuff


def textify_html(html):
    """Take a HTML string and connvert it to PLAIN text.

    What it really does: piped it to w3m -dump and read the output.
    """
    with NamedTemporaryFile(suffix=".html") as tmpf:
        print >> tmpf, html
        tmpf.flush()

        proc = Popen(['/usr/bin/w3m', '-dump', tmpf.name],
                     stdout=PIPE,
                     close_fds=True)

        output = []
        for line in proc.stdout:
            output.append(line)
        return ''.join(output)


def splitCell(cell):
    """Read the contents of a table cell and build the lecture dicts.

    Gets a BeautifulSoup element of a Cell and splits it into lectures.
    Then it builds the lecture dicts.

    The returned value is a list of lecture dicts (if any or a empty list
    else).
    """
    st = BeautifulStoneSoup(unicode(cell.renderContents('utf-8'), 'utf-8'),
                            convertEntities=BeautifulStoneSoup.HTML_ENTITIES)
    elements = unicode(st.renderContents('utf-8'),
                       'utf-8').replace('<br></br>', '\n').split('\n\n')
    lectures = []
    for elem in elements:

        lines = elem.split('\n')
        if len(lines) != 3:
            continue

        lines = map(unicode.strip, lines)
        if lines[2] != '-':
            (short, typ) = lines[1].split(" ")
            lectures.append({'short': short,
                             'typ': typ,
                             'name': lines[0],
                             'room': lines[2]})
    return lectures


def iterateRow(row):
    """Fetch the time of a table row and iterate over the data cells.

    Gets a BeautifulSoup element of a table row. First it gets the time of
    the row and then call splitCell for every cell.

    The result is a dict with a time and the lectures per day.
    """
    tds = row.findAll("td")
    row_data = {'time': None, 'days': []}
    for idx in range(0, 6):

        if idx == 0:
            row_data['time'] = unicode(tds[idx].renderContents(), 'latin1')
            continue
        row_data['days'].append(splitCell(tds[idx]))

    return row_data


def iterateTable(table):
    """Parses a whole table.

    Gets a BeautifulSoup of a table, extracts the calendar week and
    iterate over the rows. iterateRow will called for every row. The
    result will be a dict with a list of the times in correct order, a
    list of dicts containing the lectures per day and time and the
    calendar week.
    """
    rows = table.findAll("tr")
    first = True
    order = []
    days = [{} for x in range(0, 5)]
    week = None

    for row in rows:
        if first:
            week = row.findAll("td")[0].renderContents('utf-8')
            first=False
            continue

        data = iterateRow(row)
        order.append(data['time'])
        for x in range(0, 5):
            days[x][data['time']] = data['days'][x]

    return {'order': order, 'data': days, 'week': week}


def joinTables(data_list):
    """Join two or more tables.

    Gets a list of iterateTable output and join the tables. After it there
    will be only one dict like the one given from iterateTable with all
    lectures from all given datasets.

    No lecture should be double contained, even if it was in two tables.
    To ensure this the rooms per time and day have to be disjoined.
    """
    if len(data_list) == 0:
        return
    order = data_list[0]['order']
    days = [{} for x in range(0, 5)]

    for idx in range(0, len(data_list)):
        item = data_list[idx]
        data = item['data']
        for x in range(0, 5):
            for time in data[x]:
                if time not in days[x]:
                    days[x][time] = []
                for lecture in data[x][time]:
                    if lecture['room'] in [lx['room'] for lx in days[x][time]]:
                        continue
                    lecture['source'] = idx
                    days[x][time].append(lecture)

    return {'order': order, 'data': days, 'week': data_list[0]['week']}


def filterLectures(data, lectures, teacher_blacklist):
    """This filters  with a lecture whitelist and a teacher blacklist.

    The input data is a dict given from joinTables or iterateTable. The
    lecture list is a list of strings of the short names of the lectures.
    The teacher_blacklist is a list of strings of the teacher names.

    The lectures list have a magic value "all" that means all lectures and
    the teacher_blacklist have a magic value "none" that means no
    blacklist.

    The output is the same structure like joinTables and iterateTable
    have.
    """
    lectures = map(str.upper, lectures)
    teacher_blacklist = map(str.upper, teacher_blacklist)
    days = data['data']
    order = data['order']
    new_days = []
    for day in days:
        filtered = {}
        for time in order:
            filtered[time] = []
            for lecture in day[time]:
                if (lecture['short'].upper() in lectures or
                        lectures[0] == "ALL"):
                    teacher = lecture['room'].split('-')[1].strip().upper()
                    if teacher in teacher_blacklist:
                        continue
                    filtered[time].append(lecture)
        new_days.append(filtered)
    return {'order': order, 'data': new_days, 'week': data['week']}


def make_custom_table(data):
    """Generate a new HTML table from custom datasets.

    This create a new HTML table from a dict givn from joinTables,
    iterateTable or filterLectures.

    It returns the raw HTML text.
    """
    days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]
    order = data['order']
    sp = "&nbsp;"
    real = data['data']

    output = ['<table border="1", cellpadding="2">']

    output.append('<tr>')
    for x in range(0, 6):
        output.append('<td>')
        if x == 0:
            output.append(sp)
        else:
            output.append(days[x-1])
        output.append('</td>')
    output.append('</tr>')


    for time in order:
        output.append('<tr>')

        output.append('<td>')
        output.append(time.encode('utf-8'))
        output.append('</td>')

        for idx in range(0, 5):
            output.append('<td>')
            if len(real[idx][time]) == 0:
                output.append(sp)
            le_list = []
            for lecture in real[idx][time]:
                le_html = (u'%(name)s<br>%(short)s %(typ)s<br>%(room)s'
                            % lecture)
                if "source" in lecture:
                    le_html = (u'<span style="color: %s;">%s</span>'
                                % (get_color(lecture['source']), le_html))
                le_list.append(le_html)
            output.append(u'<br><br>'.join(le_list).encode('utf-8'))
            output.append('</td>')
        output.append('</tr>')
    output.append("</table>")

    return '\n'.join(output)


def make_html(headlines, data, startweek=1):
    """Make a whole new HTML page.

    The arguments are a list of headline strings, a dataset  givn from
    joinTables, iterateTable or filterLectures and a integer indicating
    the week from now, current week is 1.

    The output is raw HTML.
    """
    page = """
    <html>
      <head>
        <title>Custom Schedule</title>
        <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
      </head>
      <body>
        <h1>My custom schedule</h1>
        %s
      </body>
    </html>
    """
    output = ["<ul>"]
    for idx in range(0, len(headlines)):
        headline = headlines[idx]
        if len(headlines) > 1:
            headline = '<li style="color: %s">%s</li>' % (get_color(idx),
                                              headline.renderContents('utf-8'))
        else:
            headline = '<li>%s</li>' % (headline.renderContents('utf-8'))
        output.append(headline)

    output.append("</ul>")

    for idx in range(0, len(data)):
        output.append("<h3>Week %d (%s)</h3>" % ((idx + startweek),
                                                  data[idx]['week']))
        output.append(make_custom_table(data[idx]))

    return page % '\n'.join(output)


def split_group(text):
    """Splits studygroup strings.

    Nothing magic, just a shortcut.
    """
    return text.split('/')


def get_soups(interest_list):
    """BeautifulSoup fetching chain.

    Combination of calls to get the initial BeautifulSoup. Input is a
    studygroup string.

    Output is the output of extract_soups.
    """
    params = split_group(interest_list)
    content = get_content(*params)
    return extract_soups(content)


def make_async_map(cnt):
    """Generate a async mapping function.

    This magic is necessary because the HTW soes not allow concurrent
    starting requests. So a wait of 0.1 seconds is needed between the
    requests.

    The returned function is equivalent to map().
    """
    if cnt > 5:
        cnt = 5
    p = Pool(cnt)

    def map_async_delayed(func, iterable):

        def async_apply(item):
            res = p.apply_async(func, [item])
            time.sleep(0.1)
            return res

        def sync_get(item):
            return item.get()

        results = map(async_apply, iterable)
        return map(sync_get, results)

    return map_async_delayed


def process(interrest, lectures, teacher_blacklist):
    """Process commandline arguments an DO stuff.

    This is the commandline parsing part and the gluecode to produce the
    result.
    """
    parser = argparse.ArgumentParser(description=('Rape the HTW schedule! '
                                                  ' You can filter, combine,'
                                                  'blacklist.'))
    parser.add_argument('--html', "-v",
                        action='store_const', const=True, default=False,
                        help="Output as raw HTML!")
    parser.add_argument('--start', "-s",
                        default=1, type=int, choices=xrange(1, 5),
                        help="First Week to display.")
    parser.add_argument('--stop', "-e",
                        default=1, type=int, choices=xrange(1, 5),
                        help="Last Week to display.")
    parser.add_argument('--lectures', "-l",
                        default=lectures, type=str, nargs="+",
                        help="Lectures to filter, 'all' for ALL!")
    parser.add_argument('--courses', "-c",
                        default=interrest, type=str, nargs="+",
                        help=("Corses to show. "
                              "Format: <imma>/<courseNo>/<groupNo>."))
    parser.add_argument('--blacklist', "-b",
                        default=teacher_blacklist, type=str, nargs="+",
                        help=("Blacklist some teachers lectures. "
                              "'none' for NONE!"))


    args = parser.parse_args()
    if args.start > args.stop:
        args.stop = args.start

    my_map = map
    if len(args.courses) > 1:
        my_map = make_async_map(len(args.courses))

    soups = my_map(get_soups, interrest)

    table_data = []

    for week in range(args.start - 1, args.stop):
        tables = [iterateTable(s['table'][week]) for s in soups]
        table = joinTables(tables)
        filtered = filterLectures(table, args.lectures, args.blacklist)
        table_data.append(filtered)


    if args.html:
        print make_html([s["headline"] for s in soups], table_data, args.start)
        sys.exit(0)
    print textify_html(make_html([s["headline"] for s in soups],
                                 table_data,
                                 args.start))


if __name__ == '__main__':
    interrest = ['08/042/62',
                 '08/042/61',
                 '10/042/51']

    lectures = ['EWA',
                'BIS',
                'IM',
                'Marketing',
                'MMT',
                'Prog',
                'PS3',
                'WiMathe']

    teacher_blacklist = ['Hollas']

    process(interrest, lectures, teacher_blacklist)
