"""Minimal ROS 2 node using flight_recorder (run: python3 examples/ros2_node_example.py, Ctrl+C to stop).

The pattern for any control node:
  1. declare streams once in __init__ (fixed columns, float64);
  2. one ``append`` per tick in the hot path (numbers only);
  3. ``record`` big arrays only when they change (plans, trajectories, gains);
  4. ``save`` at shutdown; ``autosave_period`` keeps a crash from losing more than a few seconds.
"""
import math
import time

import numpy as np
import rclpy
from rclpy.node import Node

from flight_recorder import Recorder, git_commit


class ExampleController(Node):
    def __init__(self):
        super().__init__('flight_recorder_example')
        self.rec = Recorder('example_flight.h5', autosave_period=5.0,
                            metadata={'node': self.get_name(), 'git_commit': git_commit(__file__), 'rate_hz': 100.0})
        self.ticks = self.rec.stream('ticks', ['time', 'x', 'y', 'z', 'yaw', 'u_thrust', 'comp_time'], capacity=60_000)
        self.plan_seq = 0
        self.t0 = time.time()
        self.create_timer(0.01, self.control)

    def control(self):
        t = time.time() - self.t0
        c0 = time.perf_counter()
        x, y, z, yaw = math.sin(t), math.cos(t), -1.0, 0.0      # (your state estimate)
        u = 19.6 + 0.1 * math.sin(5 * t)                        # (your control law)
        self.ticks.append(t, x, y, z, yaw, u, time.perf_counter() - c0)
        if int(t * 100) % 50 == 0:                              # a new plan every 0.5 s: store it ONCE
            self.plan_seq += 1
            self.rec.record('plans', self.plan_seq, {'reference': np.column_stack([np.linspace(0, 1, 50)] * 3)},
                            {'t_start': t})


def main():
    rclpy.init()
    node = ExampleController()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        print(node.rec.save())
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
