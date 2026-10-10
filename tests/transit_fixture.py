"""Synthetic MoBY/EFA rapidJSON answers in the shape of the live answers of
10.10.2026 (field names, product classes, UTC times). No real addresses:
the origin is a made-up coordinate stop, the stops are public stop names."""
import datetime
import json

UTC = datetime.timezone.utc


def utc(hour, minute, day=10, month=10):
    return datetime.datetime(2026, month, day, hour, minute, tzinfo=UTC)


def iso(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def walk(start, minutes, to='DJK-Sportzentrum', distance=518, cls=100, seconds=None,
         origin='Startpunkt'):
    leg = dict(duration=seconds if seconds is not None else minutes * 60, distance=distance,
               isRealtimeControlled=False,
               origin=dict(name=origin, type='address', departureTimePlanned=iso(start)),
               destination=dict(name=to, type='stop', arrivalTimePlanned=iso(
                   start + datetime.timedelta(minutes=minutes))),
               transportation=dict(product={'class': cls, 'name': 'Fußweg'}))
    return leg


def ride(start, minutes, line='4', cls=4, product='Straßenbahn', origin='DJK-Sportzentrum',
         to='Rathaus', direction='Sanderau', delay=0, realtime=True, train=None, number=None,
         platform=None, arrival_platform=None, cancelled=False, estimate=True):
    end = start + datetime.timedelta(minutes=minutes)
    origin_point = dict(name=f'Würzburg, {origin}', disassembledName=origin, type='platform',
                        departureTimePlanned=iso(start))
    destination_point = dict(name=f'Würzburg, {to}', disassembledName=to, type='platform',
                             arrivalTimePlanned=iso(end))
    if estimate:
        shift = datetime.timedelta(minutes=delay)
        origin_point['departureTimeEstimated'] = iso(start + shift)
        destination_point['arrivalTimeEstimated'] = iso(end + shift)
    if platform:
        origin_point['properties'] = dict(platform=platform, platformName=f'Gleis {platform}')
    if arrival_platform:
        destination_point['properties'] = dict(platformName=f'Gleis {arrival_platform}')
    transportation = dict(name=f'{product} {line}', disassembledName=line, number=line,
                          product={'id': 1, 'class': cls, 'name': product},
                          operator=dict(name='WVV'), destination=dict(name=direction))
    if train:
        transportation['properties'] = dict(trainType=train, trainNumber=number or line)
        transportation['operator'] = dict(name='DB')
    leg = dict(duration=minutes * 60, isRealtimeControlled=realtime, origin=origin_point,
               destination=destination_point, transportation=transportation)
    if cancelled:
        leg['realtimeStatus'] = ['TRIP_CANCELLED']
    return leg


def tram_journey(dep, walk_minutes=10, **ride_args):
    """Footpath from the start to the stop, then tram 4 (like the live test to Rathaus)."""
    return dict(legs=[walk(dep - datetime.timedelta(minutes=walk_minutes), walk_minutes),
                      ride(dep, 13, **ride_args)])


def rail_journey(tram_dep, ice_dep, delay=0, realtime=True):
    """Footpath, tram 4, 7 minutes walk at the main station, ICE (like the live test
    to Nürnberg)."""
    tram_arrival = tram_dep + datetime.timedelta(minutes=9)
    return dict(legs=[
        walk(tram_dep - datetime.timedelta(minutes=10), 10),
        ride(tram_dep, 9, to='Hauptbahnhof', direction='Hauptbahnhof', realtime=realtime),
        walk(tram_arrival, 7, to='Würzburg Hbf', distance=420, cls=99, origin='Hauptbahnhof'),
        ride(ice_dep, 55, line='623', cls=16, product='ICE', origin='Würzburg Hbf',
             to='Nürnberg Hbf', direction='München Hbf', train='ICE', number='623',
             platform='4', arrival_platform='8', delay=delay, realtime=realtime),
    ])


def trips(*journeys, messages=None):
    return dict(version='10.6.21.17', journeys=list(journeys),
                systemMessages=messages or [])


def departure(dep, line, train, cls, direction, platform, delay=0, realtime=True):
    return dict(location=dict(name='Würzburg Hbf', disassembledName='Würzburg Hbf',
                              properties=dict(platform=platform)),
                departureTimePlanned=iso(dep),
                departureTimeEstimated=iso(dep + datetime.timedelta(minutes=delay)),
                isRealtimeControlled=realtime,
                transportation=dict(name=f'{train} {line}', number=line,
                                    product=dict(name=train, **{'class': cls}),
                                    destination=dict(name=direction),
                                    properties=dict(trainType=train, trainNumber=line)))


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self, limit=-1):
        return self.raw if limit < 0 else self.raw[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeClient:
    """Stands in for transit.Client in controller tests."""

    def __init__(self, journeys=(), departures=(), stops=None):
        self.journeys, self.departure_list = list(journeys), list(departures)
        self.stops = stops or {}
        self.calls = []
        self.error = None

    def trips(self, origin, destination, now, count=3):
        self.calls.append(('trips', origin, destination, now, count))
        if self.error:
            raise self.error
        import transit
        return transit.parse_trips(trips(*self.journeys))

    def departures(self, stop, now):
        self.calls.append(('departures', stop, now))
        if self.error:
            raise self.error
        import transit
        return transit.parse_departures(dict(stopEvents=self.departure_list))

    def find_stop(self, name, mode=None):
        self.calls.append(('find_stop', name, mode))
        return self.stops.get(name, dict(id='80001020', name=name.title()))
