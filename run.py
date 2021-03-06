"""Adv-net 2021 project runner"""

# get current path
import argparse
import os
import time
from logging import debug, info
from threading import Thread

from advnet_utils.links_manager import LinksManager
from advnet_utils.network_API import AdvNetNetworkAPI
from advnet_utils.sla import check_slas
from advnet_utils.topology_builder import (add_links_to_topology,
                                           build_base_topology)
from advnet_utils.traffic_manager import TrafficManager
from advnet_utils.utils import (get_user, load_constrains,
                                print_experiment_performances, wait_experiment)

cur_dir = os.path.dirname(os.path.abspath(__file__)) + "/"


def run_controllers(net: AdvNetNetworkAPI, inputidr, scenario: str,
                    log_enabled: bool = False):
    """Schedules controllers

    The controller code must be placed in `inputdir/controllers/`

    You are allowed to run a maximum of one controller per node and one global
    controller. In general, you should be able to do everything with one single
    controller.

    The global controller must be called: controller.py Per switch controllers
    must be called: <switch_name>-controller.py. For example: BAR-controller.py
    """

    # path to SLAs
    slas_file = inputidr + "/inputs/{}.slas".format(scenario)
    # path to base traffic
    base_traffic_file = inputidr + "/inputs/{}.traffic-base".format(scenario)
    # path
    controllers_dir = inputidr + "/controllers/"
    # schedule global controller if exists.
    if os.path.isfile(controllers_dir + "controller.py"):
        # set log file
        log_file = None
        if log_enabled:
            log_file = "./log/controller.log"
        net.execScript(
            'python {}/controller.py --base-traffic {} --slas {}'.format(
                controllers_dir, base_traffic_file, slas_file),
            out_file=log_file, reboot=True)
    # schedule other controllers
    for switch_name in net.p4switches():
        if os.path.isfile(
                controllers_dir + "{}-controller.py".format(switch_name)):
            # set log file
            log_file = None
            if log_enabled:
                log_file = "./log/{}-controller.log".format(switch_name)
            net.execScript('python {}/{}-controller.py --base-traffic {} --slas {}'.format(
                controllers_dir, switch_name, base_traffic_file, slas_file), log_file, reboot=True)


def program_switches(net: AdvNetNetworkAPI, inputdir):
    """Programs switches

    The p4 code must be placed in `inputdir/p4src/`

    As with the controllers, you are allowed allowed to program each switch with
    a different program. Or use the same program for every switch.

    The default P4 code must be called: switch.p4. If you want a specific switch
    to have special code you must name it <switch-name>.p4.
    """
    # path
    p4src_dir = inputdir + "/p4src/"
    for switch_name in net.p4switches():
        p4src_path = p4src_dir + "{}.p4".format(switch_name)
        if os.path.isfile(p4src_path):
            net.setP4Source(switch_name, p4src_path)
        else:  # default program
            net.setP4Source(switch_name, p4src_dir + "/switch.p4")


def run_network(
        inputdir, scenario, outputdir, debug_mode, log_enabled, pcap_enabled,
        warmup_phase=10, check_constrains=True, no_events=False,
        only_check_inputs=False):
    """Starts the project simulation"""
    # starts the flow scheduling task
    net = AdvNetNetworkAPI()
    # Network general options
    net.setLogLevel('info')
    # build base topology
    build_base_topology(net, topology_path=cur_dir + "/project/")
    # add cpu port
    # this might be useful to copy to cpu or send traffic to the switches.
    net.enableCpuPortAll()
    # set P4 programs
    program_switches(net, inputdir)
    # load constrains
    project_constrains = load_constrains(cur_dir + "/project/constrains.json")

    # add additional links
    _add_links_constrains = project_constrains["add_links_constrains"]
    _topology_path = cur_dir + "/project/"
    _links_file = inputdir + "/inputs/" + "{}.links".format(scenario)
    _added_links = add_links_to_topology(
        net, topology_path=_topology_path, links_file=_links_file,
        constrains=_add_links_constrains)

    # Assignment strategy
    net.mixed()
    # atuo assign to get more info
    net.auto_assignment()

    # Start Simulation in the future
    simulation_time_reference = time.time() + warmup_phase

    # we do no schedule events
    # schedule link failures
    _failure_constrains = project_constrains["failure_constrains"]
    _failures_file = inputdir + "/inputs/" + "{}.failure".format(scenario)
    links_manager = LinksManager(
        net, failures_file=_failures_file, constrains=_failure_constrains,
        added_links=_added_links)
    # schedules link events
    if no_events == False:
        links_manager.start(simulation_time_reference)

    # Schedule Traffic
    # clean output dir
    if outputdir == "/":
        raise Exception("Trying to remove all disk!!")
    os.system("rm -rf {}".format(outputdir))
    # get user
    _user = get_user()
    os.system("sudo -u {} mkdir -p {}".format(_user, outputdir))

    _additional_traffic_constrains = project_constrains["additional_traffic_constrains"]
    _base_traffic_constrains = project_constrains["base_traffic_constrains"]
    _additional_traffic_file = inputdir + "/inputs/" + \
        "{}.traffic-additional".format(scenario)
    _base_traffic_file = inputdir + "/inputs/" + \
        "{}.traffic-base".format(scenario)
    _slas_file = inputdir + "/inputs/" + "{}.slas".format(scenario)
    # get max traffic type for additional and base traffic to guess the experiment duration
    experiment_duration = max(_additional_traffic_constrains.get(
        "max_time", 0), _base_traffic_constrains.get("max_time", 0))
    traffic_manager = TrafficManager(
        net, _additional_traffic_file, _base_traffic_file, _slas_file,
        _additional_traffic_constrains, _base_traffic_constrains,
        check_constrains, outputdir, experiment_duration)

    # configure net waypoints
    waypoint_switches = traffic_manager.get_wp_helper().get_waypoint_switches()
    net.configure_waypoint_captures(outputdir, waypoint_switches)

    # schedule flows
    if no_events == False:
        traffic_manager.start(simulation_time_reference)

    if not only_check_inputs:
        # Adds controllers.
        run_controllers(net, inputdir, scenario, log_enabled)

        # enable or disable logs and pcaps
        if log_enabled:
            net.enableLogAll()
        else:
            net.disableLogAll()
        if pcap_enabled:  # not recommended
            net.enablePcapDumpAll()
        else:
            net.disablePcapDumpAll()

        # sets debug mode
        if debug_mode:
            # enable cli
            net.enableCli()
        else:
            # disable cli
            net.disableCli()

        # Start network
        try:
            net.startNetwork()
            # wait for experiment to finish
            if not debug_mode:
                wait_experiment(simulation_time_reference,
                                experiment_duration, outputdir, 10)
                # stop network
                info('Stopping network...\n')
                net.setLogLevel('output')
                net.net.stop()
        except Exception as e:
            # Always stop network.
            net.stopNetwork()
            print("--------------------------------")
            print("ERROR: Your setup failed to run!")
            print('--------------------------------')
            raise e
        finally:
            # Compute results, even if it failed.
            if not debug_mode:
                # print performances
                print_experiment_performances(outputdir)
                # compute, store, and print sla results
                check_slas(
                    inputdir + f"/inputs/{scenario}.slas",
                    outputdir + "results.csv",
                    outputdir + "sla.csv",
                    verbose=True
                )
            # change output dir rights since all has been written with root
            os.system("chown -R {}:{} {}".format(_user, _user, outputdir))

# MAIN Runner
# ===========


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--inputdir', help='Path to all inputs (controllers, p4src)', type=str,
        required=False, default='./')
    parser.add_argument(
        '--scenario',
        help='Path to all input events (links, failures, traffic)', type=str,
        required=False, default='test')
    parser.add_argument('--warmup', help='Time before starting the simulation',
                        type=float, required=False, default=20)
    parser.add_argument(
        '--outputdir',
        help='Path were the experiment outputs will be saved. If it exists, all content is erased',
        type=str, required=False, default='./outputs/')
    parser.add_argument(
        '--debug-mode',
        help='Runs topology indefinetely and lets you access the mininet cli',
        action='store_true', required=False, default=False)
    parser.add_argument('--log-enabled', help='Enables logging',
                        action='store_true', required=False, default=False)
    parser.add_argument(
        '--pcap-enabled', help='Enables pcap captures (not recommended)',
        action='store_true', required=False, default=False)
    parser.add_argument(
        '--no-events',
        help='Disables all link and traffic events. Useful for debugging.',
        action='store_true', required=False, default=False)

    parser.add_argument(
        '--no-constrains',
        help='Disables traffic and link constrains (only use for testing).',
        action='store_false', required=False, default=True)
    parser.add_argument(
        '--check-inputs',
        help='Only checks if input files fulfill the contrains. Does not run the network!',
        action='store_true', required=False, default=False)
    return parser.parse_args()

    # constrains are disabled if no-constrains is set.


if __name__ == "__main__":
    args = get_args()
    run_network(args.inputdir, args.scenario, args.outputdir, args.debug_mode,
                args.log_enabled, args.pcap_enabled, float(args.warmup),
                args.no_constrains, args.no_events, args.check_inputs)
