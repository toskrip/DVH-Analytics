#!/usr/bin/env python
# -*- coding: utf-8 -*-

# models.dvh.py
"""
Class to retrieve DVH data from SQL, calculate parameters dependent on DVHs, and extract plotting data
"""
# Copyright (c) 2016-2019 Dan Cutright
# This file is part of DVH Analytics, released under a BSD license.
#    See the file LICENSE included with this distribution, also
#    available at https://github.com/cutright/DVH-Analytics

from copy import deepcopy
from dateutil.parser import parse as date_parser
import numpy as np
from dvha.db.sql_connector import DVH_SQL
from dvha.db.sql_to_python import QuerySQL
import operator
from os import listdir
from os.path import join, basename, splitext
from dvha.options import Options
from dvha.paths import PROTOCOL_DIR


MAX_DOSE_VOLUME = Options().MAX_DOSE_VOLUME


# This class retrieves DVH data from the SQL database and calculates statistical DVHs (min, max, quartiles)
# It also provides some inspection tools of the retrieved data
class DVH:
    def __init__(self, uid=None, dvh_condition=None, dvh_bin_width=5):
        """
        This class will retrieve DVHs and other data in the DVH SQL table meeting the given constraints,
        it will also parse the DVH_string into python lists and retrieve the associated Rx dose
        :param uid: a list of allowed study_instance_uids in data set
        :param dvh_condition: a string in SQL syntax applied to a DVH Table query
        :param dvh_bin_width: retrieve every nth value from dvh_string in SQL
        :type dvh_bin_width: int
        """

        self.dvh_bin_width = dvh_bin_width

        if uid:
            constraints_str = "study_instance_uid in ('%s')" % "', '".join(uid)
            if dvh_condition:
                constraints_str = "(%s) and %s" % (dvh_condition, constraints_str)
        else:
            constraints_str = ''

        # Get DVH data from SQL and set as attributes
        dvh_data = QuerySQL('DVHs', constraints_str)
        if dvh_data.mrn:
            ignored_keys = {'cnx', 'cursor', 'table_name', 'constraints_str', 'condition_str'}
            self.keys = []
            for key, value in dvh_data.__dict__.items():
                if not key.startswith("__") and key not in ignored_keys:
                    if key == 'dvh_string':
                        dvh_split = [dvh.split(',')[::self.dvh_bin_width] for i, dvh in enumerate(value)]
                    setattr(self, key, value)
                    if '_string' not in key:
                        self.keys.append(key)

            # Move mrn to beginning of self.keys
            if 'mrn' in self.keys:
                self.keys.pop(self.keys.index('mrn'))
                self.keys.insert(0, 'mrn')

            # uid passed into DVH init is a unique list for querying and it is not in order of query results
            self.uid = self.study_instance_uid

            # Add these properties to dvh_data since they aren't in the DVHs SQL table
            self.count = len(self.mrn)
            self.study_count = len(set(self.uid))
            self.rx_dose = self.get_plan_values('rx_dose')
            self.sim_study_date = self.get_plan_values('sim_study_date')
            self.keys.append('rx_dose')
            self.endpoints = {'data': None,
                              'defs': None}
            self.eud = None
            self.ntcp_or_tcp = None

            self.bin_count = max([len(dvh) for dvh in dvh_split])

            self.dvh = np.zeros([self.bin_count, self.count])

            # Get needed values not in DVHs table
            for i in range(self.count):
                # Process dvh_string to numpy array, and pad with zeros at the end
                # so that all dvhs are the same length
                current_dvh = np.array(dvh_split[i], dtype='|S4').astype(np.float)
                current_dvh_max = np.max(current_dvh)
                if current_dvh_max > 0:
                    current_dvh = np.divide(current_dvh, current_dvh_max)
                zero_fill = np.zeros(self.bin_count - len(current_dvh))
                self.dvh[:, i] = np.concatenate((current_dvh, zero_fill))

            self.dth = []
            for i in range(self.count):
                # Process dth_string to numpy array
                try:
                    self.dth.append(np.array(self.dth_string[i].split(','), dtype='|S4').astype(np.float))
                except Exception:
                    self.dth.append(np.array([0]))

            # Store these now so they can be saved in DVH object without needing to query later
            with DVH_SQL() as cnx:
                self.physician_count = len(cnx.get_unique_values('Plans', 'physician',
                                                                 "study_instance_uid in ('%s')" % "','".join(self.uid)))
            self.total_fxs = self.get_plan_values('fxs')
            self.fx_dose = self.get_rx_values('fx_dose')
        else:
            self.count = 0

        self.protocols = Protocols()

    def get_plan_values(self, plan_column):
        """
        Get values from the Plans table and store in order matching mrn / study_instance_uid
        :param plan_column: name of the SQL column to be queried
        :type plan_column: str
        :return: values from the Plans table for the DVHs stored in this class
        :rtype: list
        """
        with DVH_SQL() as cnx:
            condition = "study_instance_uid in ('%s')" % "','".join(self.study_instance_uid)
            data = cnx.query('Plans', 'study_instance_uid, %s' % plan_column, condition)
            force_date = cnx.is_sqlite_column_datetime('Plans', plan_column)  # returns False for pgsql

        uids = [row[0] for row in data]
        values = [row[1] for row in data]
        if force_date:  # sqlite does not have date or time like variables
            for i, value in enumerate(values):
                try:
                    if type(value) is int:
                        values[i] = str(date_parser(str(value)))
                    else:
                        values[i] = str(date_parser(value))
                except Exception:
                    values[i] = 'None'
        return [values[uids.index(uid)] for uid in self.study_instance_uid]

    def get_rx_values(self, rx_column):
        """
        Get values from the Rxs table and store in order matching mrn / study_instance_uid
        :param rx_column: name of the SQL column to be queried
        :type rx_column: str
        :return: values from the Rxs table for the DVHs stored in this class
        :rtype: list
        """
        with DVH_SQL() as cnx:
            condition = "study_instance_uid in ('%s')" % "','".join(self.study_instance_uid)
            data = cnx.query('Rxs', 'study_instance_uid, %s' % rx_column, condition)

        uids = [row[0] for row in data]
        values = [row[1] for row in data]
        final_values = {}
        for i, uid in enumerate(uids):
            if uid in list(values):
                final_values[uid] = "%s,%s" % (final_values[uid], values[i])
            else:
                final_values[uid] = str(values[i])
        return [final_values[uid] for uid in self.study_instance_uid]

    @property
    def x_data(self):
        """
        Get x-values of the DVHs.  All DVHs stored in SQL database are 1cGy binned csv strings
        :return: x data for plotting
        :rtype: list
        """
        return [np.multiply(np.array(range(self.bin_count)), self.dvh_bin_width).tolist()] * self.count

    @property
    def y_data(self):
        """
        Get y-values of the DVHs
        :return: all DVHs in order (i.e., same as mrn, study_instance_uid)
        :rtype: list
        """
        return [self.dvh[:, i].tolist() for i in range(self.count)]

    def get_cds_data(self, keys=None):
        """
        Get data from this class in a format compatible with bokeh's ColumnDataSource.data
        :param keys: optionally specify which properties to in include
        :return: data from this class
        :rtype: dict
        """
        if not keys:
            keys = self.keys

        return deepcopy({key: getattr(self, key) for key in keys})

    def get_percentile_dvh(self, percentile):
        """
        :param percentile: the percentile to calculate for each dose-bin
        :return: a single DVH such that each bin is the given percentile of each bin over the whole sample
        :rtype: numpy 1D array
        """
        return np.percentile(self.dvh, percentile, 1)

    def get_dose_to_volume(self, volume, volume_scale='absolute', dose_scale='absolute'):
        """
        :param volume: the specified volume in cm^3
        :param volume_scale: either 'relative' or 'absolute'
        :param dose_scale: either 'relative' or 'absolute'
        :return: the dose in Gy to the specified volume
        :rtype: list
        """
        doses = np.zeros(self.count)
        for x in range(self.count):
            dvh = np.zeros(len(self.dvh))
            for y in range(len(self.dvh)):
                dvh[y] = self.dvh[y][x]
            if volume_scale == 'relative':
                doses[x] = dose_to_volume(dvh, volume, dvh_bin_width=self.dvh_bin_width)
            else:
                if self.volume[x]:
                    doses[x] = dose_to_volume(dvh, volume/self.volume[x], dvh_bin_width=self.dvh_bin_width)
                else:
                    doses[x] = 0
        if dose_scale == 'relative':
            if self.rx_dose[0]:
                doses = np.divide(doses * 100, self.rx_dose[0:self.count])
            else:
                self.rx_dose[0] = 1  # if review dvh isn't defined, the following line would crash
                doses = np.divide(doses * 100, self.rx_dose[0:self.count])
                self.rx_dose[0] = 0
                doses[0] = 0

        return doses.tolist()

    def get_volume_of_dose(self, dose, dose_scale='absolute', volume_scale='absolute'):
        """
        :param dose: input dose use to calculate a volume of dose for entire sample
        :param dose_scale: either 'absolute' or 'relative'
        :param volume_scale: either 'absolute' or 'relative'
        :return: a list of V_dose
        :rtype: list
        """
        volumes = np.zeros(self.count)
        for x in range(self.count):

            dvh = np.zeros(len(self.dvh))
            for y in range(len(self.dvh)):
                dvh[y] = self.dvh[y][x]
            if dose_scale == 'relative':
                if isinstance(self.rx_dose[x], str):
                    volumes[x] = 0
                else:
                    volumes[x] = volume_of_dose(dvh, dose * self.rx_dose[x], dvh_bin_width=self.dvh_bin_width)
            else:
                volumes[x] = volume_of_dose(dvh, dose, dvh_bin_width=self.dvh_bin_width)

        if volume_scale == 'absolute':
            volumes = np.multiply(volumes, self.volume[0:self.count])
        else:
            volumes = np.multiply(volumes, 100.)

        return volumes.tolist()

    def coverage(self, rx_dose_fraction):
        """
        :param rx_dose_fraction: relative rx dose to calculate fractional coverage
        :return: fractional coverage
        :rtype: list
        """

        answer = np.zeros(self.count)
        for x in range(self.count):
            answer[x] = self.get_volume_of_dose(float(self.rx_dose[x] * rx_dose_fraction))

        return answer

    def get_resampled_x_axis(self):
        """
        :return: the x axis of a resampled dvh
        """
        x_axis, dvhs = self.resample_dvh()
        return x_axis

    def get_stat_dvh(self, stat_type='mean', dose_scale='absolute', volume_scale='relative'):
        """
        :param stat_type: either min, mean, median, max, or std
        :param dose_scale: either 'absolute' or 'relative'
        :param volume_scale: either 'absolute' or 'relative'
        :return: a single dvh where each bin is the stat_type of each bin for the entire sample
        :rtype: numpy 1D array
        """
        if dose_scale == 'relative':
            x_axis, dvhs = self.resample_dvh()
        else:
            dvhs = self.dvh

        if volume_scale == 'absolute':
            dvhs = self.dvhs_to_abs_vol(dvhs)

        stat_function = {'min': np.min,
                         'mean': np.mean,
                         'median': np.median,
                         'max': np.max,
                         'std': np.std}
        dvh = stat_function[stat_type](dvhs, 1)

        return dvh

    def get_standard_stat_dvh(self, dose_scale='absolute', volume_scale='relative'):
        """
        :param dose_scale: either 'absolute' or 'relative'
        :param volume_scale: either 'absolute' or 'relative'
        :return: a standard set of statistical dvhs (min, q1, mean, median, q1, and max)
        :rtype: dict
        """
        if dose_scale == 'relative':
            x_axis, dvhs = self.resample_dvh()
        else:
            dvhs = self.dvh

        if volume_scale == 'absolute':
            dvhs = self.dvhs_to_abs_vol(dvhs)

        standard_stat_dvh = {'min': np.min(dvhs, 1),
                             'q1': np.percentile(dvhs, 25, 1),
                             'mean': np.mean(dvhs, 1),
                             'median': np.median(dvhs, 1),
                             'q3': np.percentile(dvhs, 75, 1),
                             'max': np.max(dvhs, 1)}

        return standard_stat_dvh

    def dvhs_to_abs_vol(self, dvhs):
        """
        :param dvhs: relative DVHs (dvh[bin, roi_index])
        :return: absolute DVHs
        :rtype: numpy 2D array
        """
        return np.multiply(dvhs, self.volume)

    def resample_dvh(self, resampled_bin_count=5000):
        """
        :return: x-axis, y-axis of resampled DVHs
        """

        min_rx_dose = np.min(self.rx_dose) * 100.
        new_bin_count = int(np.divide(float(self.bin_count), min_rx_dose) * resampled_bin_count)

        x1 = np.linspace(0, self.bin_count, self.bin_count)
        y2 = np.zeros([new_bin_count, self.count])
        for i in range(self.count):
            x2 = np.multiply(np.linspace(0, new_bin_count, new_bin_count),
                             self.rx_dose[i] * 100. / resampled_bin_count)
            y2[:, i] = np.interp(x2, x1, self.dvh[:, i])
        x2 = np.divide(np.linspace(0, new_bin_count, new_bin_count), resampled_bin_count)
        return x2, y2

    def get_summary(self):
        summary = ["Study count: %s" % self.study_count,
                   "DVH count: %s" % self.count,
                   "Institutional ROI count: %s" % len(set(self.institutional_roi)),
                   "Physician ROI count: %s" % len(set(self.physician_roi)),
                   "ROI type count: %s" % len(set(self.roi_type)),
                   "Physician count: %s" % self.physician_count,
                   "\nMin, Mean, Max",
                   "Rx dose (Gy): %0.2f, %0.2f, %0.2f" % (min(self.rx_dose),
                                                          sum(self.rx_dose) / self.count,
                                                          max(self.rx_dose)),
                   "Volume (cc): %0.2f, %0.2f, %0.2f" % (min(self.volume),
                                                         sum(self.volume) / self.count,
                                                         max(self.volume)),
                   "Min dose (Gy): %0.2f, %0.2f, %0.2f" % (min(self.min_dose),
                                                           sum(self.min_dose) / self.count,
                                                           max(self.min_dose)),
                   "Mean dose (Gy): %0.2f, %0.2f, %0.2f" % (min(self.mean_dose),
                                                            sum(self.mean_dose) / self.count,
                                                            max(self.mean_dose)),
                   "Max dose (Gy): %0.2f, %0.2f, %0.2f" % (min(self.max_dose),
                                                           sum(self.max_dose) / self.count,
                                                           max(self.max_dose))]
        return '\n'.join(summary)

    @property
    def has_data(self):
        return bool(len(self.mrn))

    def evaluate_constraint(self, index, constraint):
        """

        :param index: The index of the dvh to be evaluated (self.dvh[:, index])
        :type index: int
        :param constraint: dvh endpoint constraint
        :type constraint: Constraint
        :return: True if the dvh meets the constraint
        :rtype: bool
        """
        return constraint(self.dvh[:, index], self.mean_dose[index], self.volume[index], self.dvh_bin_width)

    def evaluate_dvh(self, index, protocol_roi, protocol_name, fractionation):
        """

        :param index: The index of the dvh to be evaluated (self.dvh[:, index])
        :type index: int
        :param protocol_roi: the name of the roi as defined in the protocol
        :type protocol_roi: str
        :param protocol_name: name of the protocol (e.g., TG101, PEMBRO)
        :type protocol_name: str
        :param fractionation: number of fractions associated with constraints, str(int) or 'Std'
        :type fractionation: str
        :return: evaluation of all constraints for the protocol_roi with provided dvh
        :rtype: dict
        """
        constraints = self.protocols.get_roi_constraints(protocol_roi, protocol_name, fractionation,
                                                         roi_type=self.roi_type[index])
        return {str(c): self.evaluate_constraint(index, c) for c in constraints}


# Returns the isodose level outlining the given volume
def dose_to_volume(dvh, rel_volume, dvh_bin_width=1):
    """
    :param dvh: a single dvh
    :param rel_volume: fractional volume
    :param dvh_bin_width: dose bin width of dvh
    :type dvh_bin_width: int
    :return: minimum dose in Gy of specified volume
    """

    # Return the maximum dose instead of extrapolating
    if rel_volume < dvh[-1]:
        return len(dvh) * dvh_bin_width * 0.01

    dose_high = np.argmax(dvh < rel_volume)
    y = rel_volume
    x_range = [dose_high - 1, dose_high]
    y_range = [dvh[dose_high - 1], dvh[dose_high]]
    dose = np.interp(y, y_range, x_range) * dvh_bin_width * 0.01

    return dose


def volume_of_dose(dvh, dose, dvh_bin_width=1):
    """
    :param dvh: a single dvh
    :param dose: dose in cGy
    :param dvh_bin_width: dose bin width of dvh
    :type dvh_bin_width: int
    :return: volume in cm^3 of roi receiving at least the specified dose
    """

    x = [int(np.floor(dose / dvh_bin_width * 100)), int(np.ceil(dose / dvh_bin_width * 100))]
    if len(dvh) < x[1]:
        return dvh[-1]
    y = [dvh[x[0]], dvh[x[1]]]
    roi_volume = np.interp(float(dose) / dvh_bin_width, x, y)

    return roi_volume


def calc_eud(dvh, a, dvh_bin_width=1):
    """
    EUD = sum[ v(i) * D(i)^a ] ^ [1/a]
    :param dvh: a single DVH as a list of numpy 1D array with 1cGy bins
    :param a: standard a-value for EUD calculations, organ and dose fractionation specific
    :param dvh_bin_width: dose bin width of dvh
    :type dvh_bin_width: int
    :return: equivalent uniform dose
    :rtype: float
    """
    v = -np.gradient(dvh)

    dose_bins = np.linspace(0, np.size(dvh), np.size(dvh))
    dose_bins = np.round(dose_bins, 3) * dvh_bin_width
    bin_centers = dose_bins - (dvh_bin_width / 2.)
    eud = np.power(np.sum(np.multiply(v, np.power(bin_centers, a))), 1. / float(a))
    eud = np.round(eud, 2) * 0.01

    return eud


def calc_tcp(gamma_50, td_tcd, eud):
    return 1. / (1. + (td_tcd / eud) ** (4. * gamma_50))


class Protocols:
    """Parse all protocols in user's PROTOCOL_DIR"""
    def __init__(self, max_dose_volume=MAX_DOSE_VOLUME):
        self.max_dose_volume = max_dose_volume

        # Collect .scp files, parse, store in self.data
        self.data = {}
        for f in self.file_paths:
            file_name = splitext(str(basename(f)))[0]
            protocol_name, fxs = tuple(file_name.split('_'))
            if protocol_name not in list(self.data):
                self.data[protocol_name] = {}
            fxs_key = fxs.lower().replace('fx', '').strip()
            self.data[protocol_name][fxs_key] = self.parse_protocol_file(f)

    @property
    def file_paths(self):
        files = listdir(PROTOCOL_DIR)
        return [join(PROTOCOL_DIR, f) for f in files if self.is_protocol_file(f) and f.count('_') == 1]

    @staticmethod
    def is_protocol_file(file_path):
        return splitext(file_path)[1].lower() == '.scp'

    @staticmethod
    def parse_protocol_file(file_path):
        constraints = {}
        current_key = None
        with open(file_path, 'r') as document:
            for line in document:
                if not(line.startswith('#') or line.strip() == ''):  # Skip line if empty or starts with #
                    if line[0] not in {'\t', ' '}:  # Constraint
                        current_key = line.strip()
                        constraints[current_key] = {}
                    else:  # OAR Name
                        line_data = line.split()
                        constraints[current_key][line_data[0]] = line_data[1]
        return constraints

    @property
    def protocol_names(self):
        return sorted(list(self.data))

    def get_fractionations(self, protocol_name):
        return [fx.replace('Fx', '') for fx in sorted(list(self.data[protocol_name]))]

    def get_rois(self, protocol_name, fractionation):
        return sorted(list(self.data[protocol_name][fractionation]))

    def get_constraints(self, protocol_name, fractionation, roi_name):
        return self.data[protocol_name][fractionation][roi_name]

    def get_column_data(self, protocol_name, fractionation):

        columns = ['Structure', 'ROI Type', 'Constraint', 'Threshold']
        data = {key: [] for key in columns}
        for roi in self.get_rois(protocol_name, fractionation):
            for constraint_label, threshold in self.get_constraints(protocol_name, fractionation, roi).items():
                if data['Structure'] and data['Structure'][-1] == roi:
                    data['Structure'].append('')
                    data['ROI Type'].append('')
                else:
                    data['Structure'].append(roi)
                    data['ROI Type'].append(self.get_roi_type(roi))
                data['Constraint'].append(constraint_label)
                data['Threshold'].append(threshold)

        return data, columns

    def get_roi_constraints(self, protocol_roi, protocol_name, fractionation, roi_type=None):
        if roi_type is None:
            roi_type = self.get_roi_type(protocol_roi)

        constraints = []
        for constraint_label, threshold in self.get_constraints(protocol_name, fractionation, protocol_roi).items():
            constraints.append(Constraint(constraint_label, threshold, roi_type=roi_type,
                                          max_dose_volume=self.max_dose_volume))

        return constraints

    @staticmethod
    def get_roi_type(roi_name):
        roi_name = roi_name.lower()
        if any([t in roi_name for t in {'gtv', 'ctv', 'itv', 'ptv'}]):
            return 'TARGET'
        return 'OAR'


class Constraint:
    def __init__(self, constraint_label, threshold, roi_type='OAR', max_dose_volume=MAX_DOSE_VOLUME):
        self.constraint_label = constraint_label
        self.threshold = threshold
        self.roi_type = roi_type
        self.max_dose_volume = max_dose_volume

    def __str__(self):
        return "%s %s %s" % (self.constraint_label, self.operator_str, self.threshold)

    def __repr__(self):
        return self.__str__()

    def __call__(self, dvh, volume, bin_width=1, mean_dose=None):
        """
        Determine if a DVH meets this constraint
        :param dvh: a relative DVH
        :type dvh: np.array
        :param volume: ROI volume
        :type volume: float
        :param bin_width: dose distance between elements of dvh, default value is 1
        :type bin_width: int
        :param mean_dose: optionally provide ROI mean dose for efficiency
        :type mean_dose: float
        :return: the pass/fail status of the dvh with this constraint
        :rtype: bool
        """
        if mean_dose is None:  # if mean dose not provided, calculate it
            diff = abs(np.diff(np.append(dvh, 0)))  # per dicompyler-core
            mean_dose = (diff.bincenters * diff.counts).sum() / diff.counts.sum()

        calc_type = self.calc_type  # calculate only once
        if calc_type == 'Volume':
            endpoint = dose_to_volume(dvh, self.input_value, dvh_bin_width=bin_width)
        elif calc_type == 'Dose':
            endpoint = volume_of_dose(dvh, self.input_value, dvh_bin_width=bin_width)
        elif calc_type == 'Mean':
            endpoint = mean_dose
        elif calc_type == 'MVS':
            endpoint = volume - volume_of_dose(dvh, self.input_value, dvh_bin_width=bin_width)
        else:
            return

        func = operator.lt if self.operator_str == '<' else operator.gt
        return func(endpoint, self.threshold_value)

    @property
    def threshold_value(self):
        if '%' in self.threshold:
            return float(self.threshold.replace('%', '')) / 100.
        return float(self.threshold)

    @property
    def string_rep(self):
        return self.__str__()

    @property
    def operator_str(self):
        if self.output_type == 'MVS':
            return ['<', '>']['OAR' in self.roi_type]
        return ['>', '<']['OAR' in self.roi_type]

    @property
    def output_type(self):
        if self.constraint_label == 'Mean':
            return 'D'
        return self.constraint_label.split('_')[0]

    @property
    def output_units(self):
        return ['Gy', 'cc'][self.output_type in {'V', 'MVS'}]

    @property
    def input(self):
        if self.constraint_label == 'Mean':
            return None
        return self.constraint_label.split('_')[1]

    @property
    def input_type(self):
        return ['Volume', 'Dose'][self.output_type in {'V', 'MVS'}]

    @property
    def calc_type(self):
        if 'MVS' in self.constraint_label:
            return 'MVS'
        if 'Mean' in self.constraint_label:
            return 'Mean'
        return self.input_type

    @property
    def input_value(self):
        if self.input is None:
            return None
        if 'max' in self.input:
            return self.max_dose_volume
        return float(self.input.replace('%', '').replace('_', ''))

    @property
    def input_scale(self):
        if self.input is None:
            return None
        return ['absolute', 'relative']['%' in self.input]

    @property
    def output_scale(self):
        return ['absolute', 'relative']['%' in self.threshold]

    @property
    def input_units(self):
        if self.input is None:
            return None
        scale = ['absolute', 'relative']['%' in self.input]
        abs_units = ['cc', 'Gy'][self.input_type == 'Dose']
        return ['%', abs_units][scale == 'absolute']
