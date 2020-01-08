#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# generated by wxGlade 0.9.3 on Thu Dec  5 13:56:20 2019
#

import wx
from dvha.models.spreadsheet import Spreadsheet
from dvha.tools.utilities import get_window_size


class StatsDataEditor(wx.Frame):
    def __init__(self, group_data, group, menu, menu_item_id, time_series, regression, control_chart):
        wx.Frame.__init__(self, None)
        self.SetSize(get_window_size(0.7, 0.6))

        self.group_data = group_data
        self.group = group
        self.menu = menu
        self.menu_item_id = menu_item_id
        self.time_series = time_series
        self.regression = regression
        self.control_chart = control_chart

        self.button = {'apply': wx.Button(self, wx.ID_ANY, "Apply"),
                       'ok': wx.Button(self, wx.ID_ANY, "OK"),
                       'cancel': wx.Button(self, wx.ID_ANY, "Cancel")}

        self.__do_bind()
        self.__set_properties()
        self.__create_data_grid()
        self.__do_layout()

        self.run()

    def __do_bind(self):
        # All buttons are bound to a function based on their key prepended with 'on_'
        # For example, query button calls on_query when clicked
        for key, button in self.button.items():
            self.Bind(wx.EVT_BUTTON, getattr(self, 'on_' + key), id=button.GetId())
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def __create_data_grid(self):
        self.grid = StatsSpreadsheet(self)

    def __set_properties(self):
        self.SetTitle("Stats Data Editor: Group %s" % self.group)

    def __do_layout(self):
        sizer_wrapper = wx.BoxSizer(wx.VERTICAL)

        sizer_buttons = wx.BoxSizer(wx.HORIZONTAL)
        sizer_buttons.Add(self.button['apply'], 0, wx.ALL, 5)
        sizer_buttons.Add(self.button['ok'], 0, wx.ALL, 5)
        sizer_buttons.Add(self.button['cancel'], 0, wx.ALL, 5)

        sizer_wrapper.Add(sizer_buttons, 0, wx.EXPAND, 5)
        sizer_wrapper.Add(self.grid, 1, wx.EXPAND, 0)

        self.SetSizer(sizer_wrapper)
        self.Center()
        self.Layout()

    def run(self):
        self.toggle_data_menu_item()
        self.Show()

    def on_close(self, *args):
        self.toggle_data_menu_item()
        self.Destroy()

    def on_apply(self, *args):
        self.grid.update_stats_data()

    def on_ok(self, *args):
        self.grid.update_stats_data()
        self.on_close()

    def on_cancel(self, *args):
        self.on_close()

    def toggle_data_menu_item(self):
        short_cut = self.group + 4
        show_hide = ['Show', 'Hide']['Show' in self.menu.GetLabel(self.menu_item_id)]
        label = "%s Stats Data: Group %s" % (show_hide, self.group)
        self.menu.SetLabel(self.menu_item_id, '%s\tCtrl+%s' % (label, short_cut))

    def update_time_series(self):
        self.time_series.initialize_y_axis_options()
        self.time_series.update_plot()

    def update_regression(self):
        self.regression.update_combo_box_choices()
        self.regression.update_plot()

    def update_control_chart(self):
        self.control_chart.update_combo_box_y_choices()
        self.control_chart.update_plot()

    def update_chart_models(self):
        self.update_time_series()
        self.update_regression()
        self.update_control_chart()


class StatsSpreadsheet(Spreadsheet):
    def __init__(self, parent):
        Spreadsheet.__init__(self, parent)

        self.parent = parent
        self.group = parent.group
        self.stats_data = {grp: parent.group_data[grp]['stats_data'] for grp in [1, 2]}

        self.__initialize_grid()

    def __initialize_grid(self):
        column_labels = [label for label in self.stats_data[self.group].data.keys() if 'date' not in label.lower()]
        column_labels.sort()

        self.CreateGrid(len(self.stats_data[self.group].mrns)+1, len(column_labels)+3)

        self.SetCellValue(0, 0, 'MRN')
        self.SetCellValue(0, 1, 'Study Instance UID')
        self.SetCellValue(0, 2, 'Sim Study Date')

        for row, mrn in enumerate(self.stats_data[self.group].mrns):
            self.SetCellValue(row+1, 0, mrn)
            self.SetCellValue(row+1, 1, self.stats_data[self.group].uids[row])
            self.SetCellValue(row+1, 2, self.stats_data[self.group].sim_study_dates[row])

        # self.SetColMinimalAcceptableWidth(1000)
        for col, label in enumerate(column_labels):
            # self.SetColMinimalWidth(col+2, 1000)
            self.SetCellValue(0, col+3, label)
            for row, value in enumerate(self.stats_data[self.group].data[label]['values']):
                self.SetCellValue(row+1, col+3, str(value))

    def update_stats_data(self):

        for col in range(self.GetNumberCols()):
            label = self.GetCellValue(0, col)
            if label.lower() not in ['mrn', 'study instance uid', 'sim study date']:
                if label not in list(self.stats_data[self.group].data):
                    values = ['None'] * (self.GetNumberRows()-1)
                    self.stats_data[self.group].add_variable(label, values)

                    # Add column to other stats data object
                    other = 3 - self.group
                    if self.stats_data[other] and label not in list(self.stats_data[other].data):
                        values = ['None'] * len(self.stats_data[other].mrns)
                        self.stats_data[other].add_variable(label, values)

                data = [self.convert_value(row+1, col) for row in range(self.GetNumberRows()-1)]
                self.stats_data[self.group].set_variable_data(label, data)
        self.parent.update_chart_models()

    def get_column_data(self, column):
        return [self.convert_value(row+1, column) for row in range(self.GetNumberRows()-1)]

    def get_custom_time_series_data(self, column):
        return {'y': self.get_column_data(column),
                'mrn': [self.GetCellValue(row+1, 0) for row in range(self.GetNumberRows()-1)],
                'uid': [self.GetCellValue(row+1, 1) for row in range(self.GetNumberRows()-1)]}

    def convert_value(self, row, col):
        value = self.GetCellValue(row, col)
        try:
            return float(value)
        except ValueError:
            return 'None'

