import json
import sys

import settings
from typing import Union, Tuple

from datetime import datetime as d
from datetime import timedelta as dl
from random import choice
from requests import request
from urllib.parse import urlencode


class YandexRaspAPI:

    API_URL = settings.API_URL

    def get_api_data(self, api_request, param) -> Tuple[int, Union[dict, bytes]]:
        with open(settings.API_KEY, 'r') as f:
            api_key = f.read()

        url = f'{self.API_URL}/{api_request}/?{urlencode(param)}'
        r = request(method='GET', url=url, headers={'Content-Type': 'application/json', 'Authorization': api_key})

        if r.status_code == 200:
            return r.status_code, r.json()
        else:
            return r.status_code, r.content

    def get_api_station_list(self):
        status_code, station_list_request = self.get_api_data(api_request='stations_list', param={'lang': 'ru_RU'})

        if status_code == 200:
            try:
                return station_list_request
            except KeyError:
                pass
        else:
            print(status_code, str(station_list_request))
            return None

    def get_api_search(self, departure_station, arrival_station, date):
        url_param = {
            'lang': 'ru_RU',
            'from': departure_station,
            'to': arrival_station,
            'date': date,
            'transfers': True,
        }

        status_code, search_request = self.get_api_data(api_request='search', param=url_param)

        if status_code == 200:
            try:
                return search_request
            except KeyError:
                pass
        else:
            print(status_code, search_request)

    def get_regions(self) -> list:
        api_data = self.get_api_station_list()

        for countries in api_data['countries']:
            if countries['title'] == settings.COUNTRY:
                return countries['regions']

    def get_region_settlements(self) -> list:
        for regions in self.get_regions():
            if regions['title'] == settings.REGIONS:
                return regions['settlements']

    def get_regions_title(self) -> str:
        return '\n'.join(sorted([regions['title'] for regions in self.get_regions()]))

    def generate_station_list(self) -> None:
        suburban_stations = [
            self.clear_station_data(station) for locality in self.get_region_settlements() for station in locality['stations']
        ]

        with open(settings.SUBURBAN_STATIONS_FILE, 'w+') as fl:
            json.dump(suburban_stations, fl)

    def clear_station_data(self, station_data: dict) -> dict:
        if station_data['transport_type'] == 'train' and station_data['title'] not in settings.EXCLUDED_STATION \
                and station_data['codes']['yandex_code'] not in settings.EXCLUDED_STATION \
                and station_data['direction'] in settings.DIRECTION \
                and (station_data['latitude'] and station_data['longitude']):

            return {
                'yandex_code': station_data['codes']['yandex_code'],
                'direction': station_data['direction'],
                'latitude': round(station_data['latitude'], 6),
                'longitude': round(station_data['longitude'], 6),
                'title': station_data['title'],
            }

        return {}


class ApiOSRM:

    API_URL = settings.API_OSRM

    def get_api_data(self, coordinates: tuple) -> tuple:
        start_long, start_lat, finish_long, finish_lat = coordinates

        url = f'{self.API_URL}/{start_long},{start_lat};{finish_long},{finish_lat}?overview=false'
        r = request(method='GET', url=url, headers={'Content-Type': 'application/json'})

        if r.status_code == 200:
            return r.status_code, r.json()
        else:
            return r.status_code, r.content

    def get_distance(self, coordinates: tuple) -> Union[float, None]:
        status_code, distance_request = self.get_api_data(coordinates)

        if status_code == 200:
            try:
                return distance_request['routes'][0]['distance']
            except KeyError:
                pass
        else:
            print(status_code, distance_request)
            return None


class Trip:

    def __init__(self, duration_in_night, quick_trip):
        self.duration = duration_in_night
        self.quick_trip = quick_trip
        self.trip = None

    def choice_trip(self):
        stations = self.open_suburban_stations()
        trip_variants = [(choice(stations), choice(stations)) for _ in range(1000)]
        request_counter = 0

        while request_counter < 20:
            request_counter += 1
            start, finish = choice(trip_variants)
            osrm = ApiOSRM()
            distance = osrm.get_distance((start['longitude'], start['latitude'], finish['longitude'], finish['latitude']))

            if distance:
                start_timetable = self.get_start_timetable(start['yandex_code'])
                finish_timetable = self.get_finish_timetable(finish['yandex_code'])

                if start_timetable and finish_timetable:
                    start_time = sorted(start_timetable, key=lambda departure: departure['departure'])
                    finish_time = sorted(finish_timetable, key=lambda departure: departure['departure'])

                    first_start_time = start_time[0]
                    end_finish_time = finish_time[0]

                    start_arrival = d.fromisoformat(first_start_time['arrival'])
                    finish_departure = d.fromisoformat(end_finish_time['departure'])
                    start_dl = dl(days=start_arrival.day, hours=start_arrival.hour, minutes=start_arrival.minute,
                                  weeks=start_arrival.isocalendar().week)
                    finish_dl = dl(days=finish_departure.day, hours=finish_departure.hour, minutes=finish_departure.minute,
                                   weeks=finish_departure.isocalendar().week)

                    trip_duration = ((finish_dl - start_dl).total_seconds() // 60 // 60)

                    if (trip_duration - settings.REST_TIME_IN_TRIP * self.duration) * settings.SPEED > distance // 1000:

                        return {
                            'first_start_time': first_start_time,
                            'end_finish_time': end_finish_time,
                            'start': start,
                            'start_time': start_time,
                            'finish': finish,
                            'finish_time': finish_time,
                            'distance': distance // 1000,
                            'trip_duration': trip_duration
                        }
            else:
                break

    def open_suburban_stations(self):
        with open(settings.SUBURBAN_STATIONS_FILE, 'r') as fl:
            suburban_stations = json.load(fl)

        return [station for station in suburban_stations if station]

    def get_start_timetable(self, start_station: str) -> list:
        ya_api = YandexRaspAPI()
        now = d.now()
        start_timetable = ya_api.get_api_search(settings.HOME_POINT_YANDEX_CODE, start_station, d.today().date().isoformat())
        result = []

        try:
            for segment in start_timetable['segments']:
                departure_t = d.fromisoformat(segment['departure'])
                departure_dl = dl(hours=departure_t.hour, minutes=departure_t.minute) - dl(hours=now.hour, minutes=now.minute)

                if departure_dl.days >= 0 and departure_dl.seconds >= settings.TIME_FROM_HOME_TO_STATION * 60:
                    result.append(self.append_segment(segment))
        except (KeyError, TypeError):
            return []
        else:
            return result

    def get_finish_timetable(self, finish_station: str) -> list:
        ya_api = YandexRaspAPI()
        finish_timetable = ya_api.get_api_search(
            finish_station, settings.HOME_POINT_YANDEX_CODE, (d.today() + dl(days=self.duration)).date().isoformat()
        )

        try:
            if self.quick_trip:
                result = [self.append_segment(finish_timetable['segments'].pop())]
            else:
                result = [self.append_segment(segment) for segment in finish_timetable['segments']]
        except (KeyError, TypeError, IndexError):
            return []
        else:
            return result

    def format_time(self, time_str: str) -> str:
        t = d.fromisoformat(time_str)
        return f'{t.month:0>2}-{t.day:0>2} {t.hour:0>2}:{t.minute:0>2}'

    def append_segment(self, segment: dict) -> dict:
        result = {
            'departure': segment['departure'],
            'arrival': segment['arrival'],
            'duration': int(segment['duration'] // 60),
        }

        if segment['has_transfers']:
            result['transfers_detail'] = []

            for details in segment['details']:
                try:
                    result['transfers_detail'].append(
                        {
                            'departure': details['departure'],
                            'arrival': details['arrival'],
                            'duration': int(details['duration'] // 60),
                            'from': details['from']['title'],
                            'start_date': details['start_date'],
                            'to': details['to']['title'],
                        }
                    )
                except KeyError:
                    pass

            result['transfers'] = [transfers['title'] for transfers in segment['transfers']]

        return result

    def _print_trip_dict(self, trip_dict):
        for k, v in trip_dict.items():

            if k in ('departure', 'arrival'):
                v = self.format_time(v)

            print(k, v)
        print('')

    def print_trip(self):

        break_line = '-' * 35

        print(f'{"Ближайший электрон до ст.".upper()} {self.trip["start"]["title"]}')
        self._print_trip_dict(self.trip['first_start_time'])

        print(break_line)
        print(f'{"Последний электрон от ст.".upper()} {self.trip["finish"]["title"]}')
        self._print_trip_dict(self.trip['end_finish_time'])

        print(break_line)
        print('Расписание до Станции прибытия'.upper())
        for row in self.trip['start_time']:
            self._print_trip_dict(row)

        print(break_line)
        print('Расписание от Станции отправления'.upper())
        for row in self.trip['finish_time']:
            self._print_trip_dict(row)

        print(break_line)
        print(f'{"Расчетное расстояние".upper()}: {round(self.trip["distance"], 2)} км.')

        print(break_line)
        print(f'{"Продолжительность поездки".upper()}: {self.trip["trip_duration"]} ч.')


if __name__ == '__main__':
    sys_args = [a for a in sys.argv]

    if '-gs' in sys_args:
        yandex_API = YandexRaspAPI()
        yandex_API.generate_station_list()

    if '-t' in sys_args:
        night_in_trip = settings.DEFAULT_TRIP_NIGHT
        quick_trip_setting = False

        if '-n' in sys_args:
            try:
                night_in_trip = int(sys_args[sys_args.index('-n') + 1])
            except (ValueError, IndexError):
                exit('After "-n" need argument value: int')

        if '-q' in sys_args:
            quick_trip_setting = True
            night_in_trip = 0

        trip = Trip(duration_in_night=night_in_trip, quick_trip=quick_trip_setting)
        trip.trip = trip.choice_trip()

        if trip.trip:
            trip.print_trip()
