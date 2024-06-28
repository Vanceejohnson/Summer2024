#!/usr/bin/python
# Software License Agreement (BSD License)
#
# Copyright (c) 2013, Juergen Sturm, TUM
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of TUM nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# Requirements:
# sudo apt-get install python-argparse

"""
Modified and extended by Matthias Mueller - Intel Intelligent Systems Lab - 2020
The controls are event-based and not synchronized to the frames.
This script matches the control signals to frames.
Specifically, if there was no control signal event within some threshold (default: 1ms),
the last control signal before the frame is used.
"""

import functools
import os
import glob
import re
import multiprocessing as mp

from klogs import kLogger
TAG = "OPENBOT"
log = kLogger(TAG)


###### include from utils.py ######

def list_dirs(path : str) -> list:
    ''' 
    Lists all directories in a path
    Args:
        path (str): path to directory
    Returns:
        list: list of directories
    '''
    return [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]

###### end utils.py ######


###### include from associate_frames.py ######

def read_file_list(filename):
    """
    Reads a trajectory from a text file.

    File format:
    The file format is "stamp d1 d2 d3 ...", where stamp denotes the time stamp (to be matched)
    and "d1 d2 d3.." is arbitary data (e.g., a 3D position and 3D orientation) associated to this timestamp.

    Input:
    filename -- File name

    Output:
    dict -- dictionary of (stamp,data) tuples

    """
    f = open(filename)
    # discard header
    header = f.readline()
    data = f.read()
    lines = data.replace(",", " ").replace("\t", " ").split("\n")
    data = [
        [v.strip() for v in line.split(" ") if v.strip() != ""]
        for line in lines
        if len(line) > 0 and line[0] != "#"
    ]
    data = [(int(line[0]), line[1:]) for line in data if len(line) > 1]
    return dict(data)


def associate(first_list, second_list, max_offset):
    """
    Associate two dictionaries of (stamp,data). As the time stamps never match exactly, we aim
    to find the closest match for every input tuple.

    Input:
    first_list -- first dictionary of (stamp,data) tuples
    second_list -- second dictionary of (stamp,data) tuples
    offset -- time offset between both dictionaries (e.g., to model the delay between the sensors)
    max_difference -- search radius for candidate generation

    Output:
    matches -- list of matched tuples ((stamp1,data1),(stamp2,data2))

    """
    first_keys = list(first_list)
    second_keys = list(second_list)
    potential_matches = [
        (b - a, a, b) for a in first_keys for b in second_keys if (b - a) < max_offset
    ]  # Control before image or within max_offset
    potential_matches.sort(reverse=True)
    matches = []
    for diff, a, b in potential_matches:
        if a in first_keys and b in second_keys:
            first_keys.remove(a)  # Remove frame that was assigned
            matches.append((a, b))  # Append tuple

    matches.sort()
    return matches

def associate_ios(control, img, max_offset):
    """
    Associate two dictionaries of (stamp,data). As the time stamps never match exactly, we aim
    to find the closest match for every input tuple.

    Input:
    control -- control file
    img -- image folder
    offset -- time offset between both dictionaries (e.g., to model the delay between the sensors)

    Output:
    matches -- list of matched tuples ((stamp1,data1),(stamp2,data2))

    """
    ts = []
    steer = []
    thr = []
    with open(control, "r") as fd:
        lines = fd.readlines()
    for line in lines:
        a = line.split(", ")
        ts.append(int(a[0]))
        steer.append(int(a[1]))
        thr.append(int(a[2].split("\n")[0]))
    img_files = glob.glob(img + "/*.jpeg")
    img_files.sort()
    i_ts = [int(re.findall(r'\d+', i)[-1]) for i in img_files]
    matches = []
    for time in i_ts:
        try:
            ind = ts.index(time)
            matches.append((time, ts[ind]))
        except ValueError:
            continue
    matches.sort()
    return matches, i_ts, thr, steer


def match_frame_ctrl_cmd(datasets : list, max_offset : int, train_or_test : str, redo_matching : bool = False, remove_zeros : bool = True, ios : bool = False) -> list:
    '''
    Matches the control signals to frames.
    Specifically, if there was no control signal event within some threshold (default: 1ms),
    the last control signal before the frame is used.

    Args:
        datasets (str): path to dataset directory
        max_offset (int): maximum offset between control signal and frame
        train_or_test (str): train or test
        redo_matching (bool): redo matching
        remove_zeros (bool): remove zero control signals
        ios (bool): use ios data

    Returns:
        list: list of tuples (frame, control signal)
    '''

    match_frame_session_wrap = functools.partial(
        match_frame_session,
        max_offset=max_offset,
        train_or_test=train_or_test,
        redo_matching=redo_matching,
        remove_zeros=remove_zeros,
        ios=ios,
    )

    session_dirs = [
        os.path.join(data_dir, dataset, folder)
        for data_dir in datasets
        for dataset in list_dirs(data_dir)
        for folder in list_dirs(os.path.join(data_dir, dataset))
    ]

    log.info(f"Datasets: {len(session_dirs)}")

    with mp.Pool(min(mp.cpu_count(), len(session_dirs))) as pool:
        frame_lists = pool.map(match_frame_session_wrap, session_dirs)

    frames = [
        frame_list[timestamp][0]
        for frame_list in frame_lists
        for timestamp in list(frame_list)
    ]
    return frames


def match_frame_session(session_dir : str, max_offset : int, train_or_test : str, redo_matching : bool = True, remove_zeros : bool = True, ios : bool = False) -> dict:
    '''
    Matches the control signals to frames. Does the heavy lifting for match_frame_ctrl_cmd.

    Args:
        session_dir (str): path to session directory
        max_offset (int): maximum offset between control signal and frame
        train_or_test (str): train or test
        redo_matching (bool): redo matching
        remove_zeros (bool): remove zero control signals
        ios (bool): use ios data

    Returns:
        dict: dictionary of tuples (frame, control signal)
    '''
    if not ios:
        sensor_path = os.path.join(session_dir, "sensor_data")
        img_path = os.path.join(session_dir, "images")
        log.info("Processing folder %s" % (session_dir))
        if not redo_matching and os.path.isfile(os.path.join(sensor_path, "matched_frame_ctrl.txt")):
            log.info("Frames and controls already matched.")
        else:
            # Match frames with control signals
            frame_list = read_file_list(os.path.join(sensor_path, "rgbFrames.txt"))
            if len(frame_list) == 0:
                raise Exception("Empty rgbFrames.txt")
            ctrl_list = read_file_list(os.path.join(sensor_path, "ctrlLog.txt"))
            if len(ctrl_list) == 0:
                raise Exception("Empty ctrlLog.txt")
            matches = associate(frame_list, ctrl_list, max_offset)
            with open(os.path.join(sensor_path, "matched_frame_ctrl.txt"), "w") as f:
                f.write("timestamp (frame),time_offset (ctrl-frame),frame,left,right\n")
                for a, b in matches:
                    f.write(
                        "%d,%d,%s,%s\n"
                        % (
                            a,
                            b - a,
                            ",".join(frame_list[a]),
                            ",".join(ctrl_list[b]),
                        )
                    )
            log.info("Frames and controls matched.")
    else:
        control_path = os.path.join(session_dir, "control")
        img_path = os.path.join(session_dir, "images")
        log.info("Processing folder %s" % (session_dir))
        if not redo_matching and os.path.isfile(os.path.join(session_dir, "matched_frame_ctrl.txt")):
            log.info("Frames and controls already matched.")
        else:
            matches, imgs, thr, steer = associate_ios(control_path, img_path, max_offset)
            with open(os.path.join(session_dir, "matched_frame_ctrl.txt"), "w") as f:
                f.write("timestamp (frame),time_offset (ctrl-frame),frame,left,right\n")
                for i, a in enumerate(matches):
                    f.write(
                        "%d,%d,%s,%s\n"
                        % (
                            a[0],
                            a[1] - a[0],
                            f"{imgs[i]}",
                            f"{thr[i]},{steer[i]}",
                        )
                    )
            log.info("Frames and controls matched.")

    if not redo_matching and os.path.isfile(
        os.path.join(sensor_path, "matched_frame_ctrl_cmd.txt")
    ):
        log.info("Frames and commands already matched.")
    else:
        # Match frames and controls with indicator commands
        if not ios:
            frame_list = read_file_list(os.path.join(sensor_path, "matched_frame_ctrl.txt"))
            if len(frame_list) == 0:
                raise Exception("Empty matched_frame_ctrl.txt")
            cmd_list = read_file_list(os.path.join(sensor_path, "indicatorLog.txt"))
            if len(cmd_list) == 0 or sorted(frame_list)[0] < sorted(cmd_list)[0]:
                cmd_list[sorted(frame_list)[0]] = ["0"]
            matches = associate(frame_list, cmd_list, max_offset)
            with open(os.path.join(sensor_path, "matched_frame_ctrl_cmd.txt"), "w") as f:
                f.write(
                    "timestamp (frame),time_offset (cmd-frame),time_offset (ctrl-frame),frame,left,right,cmd\n"
                )
                for a, b in matches:
                    f.write(
                        "%d,%d,%s,%s\n"
                        % (a, b - a, ",".join(frame_list[a]), ",".join(cmd_list[b]))
                    )
            log.info("Frames and commands matched.")
        else:
            frame_list = read_file_list(os.path.join(session_dir, "matched_frame_ctrl.txt"))
            if len(frame_list) == 0:
                raise Exception("Empty matched_frame_ctrl.txt")
            with open(os.path.join(session_dir, "matched_frame_ctrl_cmd.txt"), "w") as f:
                f.write(
                    "timestamp (frame),time_offset (cmd-frame),time_offset (ctrl-frame),frame,left,right,cmd\n"
                )
                for key in frame_list:
                    f.write(
                        "%s,%s,%s,%s\n"
                        % (key, frame_list[key][0], f"0,{key}", f"{frame_list[key][2]},{frame_list[key][3]},0")
                    )
            log.info("Frames and commands matched.")
        # Set indicator signal to 0 for initial frames

    if not ios:
        if not redo_matching and os.path.isfile(
            os.path.join(sensor_path, "matched_frame_ctrl_cmd_processed.txt")
        ):
            log.info("Preprocessing already completed.")
        else:
            # Cleanup: Add path and remove frames where vehicle was stationary
            frame_list = read_file_list(
                os.path.join(sensor_path, "matched_frame_ctrl_cmd.txt")
            )
            with open(
                os.path.join(sensor_path, "matched_frame_ctrl_cmd_processed.txt"), "w"
            ) as f:
                f.write("timestamp,frame,left,right,cmd\n")
                for timestamp in list(frame_list):
                    frame = frame_list[timestamp]
                    if len(frame) < 6:
                        continue
                    left = int(frame[3])
                    right = int(frame[4])
                    if remove_zeros and left == 0 and right == 0:
                        log.debug(f"Removed timestamp: {timestamp}")
                        del frame
                    else:
                        if train_or_test == "train":
                            frame_name = os.path.join(img_path, frame[2] + "_preview.jpeg")
                        else:
                            frame_name = os.path.join(img_path, frame[2] + "_crop.jpeg")
                        cmd = int(frame[5])
                        f.write(
                            "%s,%s,%d,%d,%d\n" % (timestamp, frame_name, left, right, cmd)
                        )
            log.info("Preprocessing completed.")

        return read_file_list(
            os.path.join(sensor_path, "matched_frame_ctrl_cmd_processed.txt")
        )
    else:
        if not redo_matching and os.path.isfile(
            os.path.join(session_dir, "matched_frame_ctrl_cmd_processed.txt")
        ):
            log.info("Preprocessing already completed.")
        else:
            # Cleanup: Add path and remove frames where vehicle was stationary
            frame_list = read_file_list(
                os.path.join(session_dir, "matched_frame_ctrl_cmd.txt")
            )
            with open(os.path.join(session_dir, "matched_frame_ctrl_cmd_processed.txt"), "w") as f:
                f.write("timestamp,frame,left,right,cmd\n")
                for timestamp in list(frame_list):
                    frame = frame_list[timestamp]
                    if len(frame) < 6:
                        continue
                    left = float(int(frame[3]) * 2 / 255) - 1
                    right = float(int(frame[4]) * 2 / 255) - 1
                    if remove_zeros and left == 0 and right == 0:
                        log.debug(f"Removed timestamp: {timestamp}")
                        del frame
                    else:
                        if train_or_test == "train":
                            frame_name = os.path.join(img_path, f"{frame[2]}.jpeg")
                        else:
                            frame_name = os.path.join(img_path, f"{frame[2]}.jpeg")
                        cmd = int(frame[5])
                        f.write(
                            "%s,%s,%f,%f,%d\n" % (timestamp, frame_name, left, right, cmd)
                        )
            log.info("Preprocessing completed.")

        return read_file_list(
            os.path.join(session_dir, "matched_frame_ctrl_cmd_processed.txt")
        )


###### end associate_frames.py ######


####### include from tfrecord.py #######

def load_labels(data_dir : str, dataset_folders : list, ios : bool = False) -> dict:
    '''
    Returns a dictionary of matched images path[string] and actions tuple (throttle[int], steer[int]).

    Args:
        data_dir (str): Path to the dataset folder.
        dataset_folders (list): List of folders to load.
        ios (bool): Whether to load iOS dataset or not.

    Returns:
        dict : Dictionary of matched images path[string] and actions tuple (throttle[int], steer[int]).
    '''
    corpus = []
    for folder in dataset_folders:
        if not ios:
            sensor_data_dir = os.path.join(data_dir, folder, "sensor_data")
            with open(
                os.path.join(sensor_data_dir, "matched_frame_ctrl_cmd_processed.txt")
            ) as f_input:
                header = f_input.readline()  # discard header
                data = f_input.read()
                lines = (
                    data.replace(",", " ")
                    .replace("\\", "/")
                    .replace("\r", "")
                    .replace("\t", " ")
                    .split("\n")
                )
                data = [
                    [v.strip() for v in line.split(" ") if v.strip() != ""]
                    for line in lines
                    if len(line) > 0 and line[0] != "#"
                ]
                # Tuples containing id: framepath and label: throttle, steer
                data = [(l[1], l[2:4]) for l in data if len(l) > 1]
                corpus.extend(data)
        else:
            sensor_data_dir = data_dir._str #os.path.join(data_dir, folder)
            with open(
                os.path.join(sensor_data_dir, "control")
            ) as f_input:
                data = f_input.read()
                lines = (
                    data.replace(",", " ")
                    .replace("\\", "/")
                    .replace("\r", "")
                    .replace("\t", " ")
                    .split("\n")
                )
                data = [
                    [v.strip() for v in line.split(" ") if v.strip() != ""]
                    for line in lines
                    if len(line) > 0 and line[0] != "#"
                ]
                # Tuples containing id: framepath and label: throttle, steer
                data = [(sensor_data_dir + "/images/" + l[0] + ".jpeg", l[1:3]) for l in data if len(l) > 1]
                corpus.extend(data)
        
        
    return dict(corpus)

###### end tfrecord.py ######

def load_labels_map(data_dir : str, dataset_folders : list, ios : bool = False) -> dict:
    '''
    Returns a dictionary of matched images path[string] and actions tuple (throttle[int], steer[int]).

    Args:
        data_dir (str): Path to the dataset folder.
        dataset_folders (list): List of folders to load.
        ios (bool): Whether to load iOS dataset or not.

    Returns:
        dict : Dictionary of matched images path[string] and actions tuple (throttle[int], steer[int]).
    '''
    corpus = []
    sensor_data_dir = data_dir._str #os.path.join(data_dir, folder)
    with open(
        os.path.join(sensor_data_dir, "control")
    ) as f_input:
        data = f_input.read()
        lines = (
            data.replace(",", " ")
            .replace("\\", "/")
            .replace("\r", "")
            .replace("\t", " ")
            .split("\n")
        )
        data = [
            [v.strip() for v in line.split(" ") if v.strip() != ""]
            for line in lines
            if len(line) > 0 and line[0] != "#"
        ]

        with open(
            os.path.join(sensor_data_dir, "motion")
        ) as f_input:
            dataM = f_input.read()
            lines = (
                dataM.replace(",", " ")
                .replace("\\", "/")
                .replace("\r", "")
                .replace("\t", " ")
                .split("\n")
            )
            dataM = [
                [v.strip() for v in line.split(" ") if v.strip() != ""]
                for line in lines
                if len(line) > 0 and line[0] != "#"
            ]
            # Tuples containing id: framepath, depthpath, and label: throttle, steer, label : accx, accy, accz
            for x,y in zip(data,dataM):
                #data = [(sensor_data_dir + "/images/" + l[0] + ".jpeg", sensor_data_dir + "/depth/" + l[0] + ".jpeg", l[1:3]) for l in data if len(l) > 1]
                corpus.append((sensor_data_dir + "/images/" + x[0] + ".jpeg", sensor_data_dir + "/depth/" + x[0] + ".jpeg", x[1:3], y[1:4]))
    return corpus
