"""
Unit tests for the Traffic Management System.
Run with: python -m pytest tests/test_controller.py -v

Powered by Liveupx.com & xHost
"""

import sys
import os
import time

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_management_system import (
    AdaptiveTrafficController, Intersection, TrafficLane, TrafficSignal,
    Vehicle, VehicleType, Direction, SignalState, LogEntry
)


class TestVehicle:
    def test_create_car(self):
        v = Vehicle(1, VehicleType.CAR, Direction.NORTH, time.time())
        assert v.is_emergency is False
        assert v.vehicle_type == VehicleType.CAR

    def test_create_emergency(self):
        v = Vehicle(2, VehicleType.EMERGENCY, Direction.EAST, time.time())
        assert v.is_emergency is True

    def test_default_wait_time(self):
        v = Vehicle(3, VehicleType.BUS, Direction.SOUTH, time.time())
        assert v.wait_time == 0.0


class TestTrafficLane:
    def test_add_vehicle(self):
        lane = TrafficLane(direction=Direction.NORTH)
        v = Vehicle(1, VehicleType.CAR, Direction.NORTH, time.time())
        lane.add_vehicle(v)
        assert len(lane.queue) == 1
        assert lane.total_vehicles_entered == 1

    def test_release_vehicle(self):
        lane = TrafficLane(direction=Direction.NORTH)
        v = Vehicle(1, VehicleType.CAR, Direction.NORTH, time.time())
        lane.add_vehicle(v)
        released = lane.release_vehicle()
        assert released is not None
        assert released.vehicle_id == 1
        assert len(lane.queue) == 0
        assert lane.total_vehicles_passed == 1

    def test_release_empty_lane(self):
        lane = TrafficLane(direction=Direction.EAST)
        assert lane.release_vehicle() is None

    def test_fifo_order(self):
        lane = TrafficLane(direction=Direction.WEST)
        for i in range(5):
            lane.add_vehicle(Vehicle(i, VehicleType.CAR, Direction.WEST, time.time()))
        for i in range(5):
            v = lane.release_vehicle()
            assert v.vehicle_id == i

    def test_has_emergency(self):
        lane = TrafficLane(direction=Direction.SOUTH)
        lane.add_vehicle(Vehicle(1, VehicleType.CAR, Direction.SOUTH, time.time()))
        assert lane.has_emergency() is False
        lane.add_vehicle(Vehicle(2, VehicleType.EMERGENCY, Direction.SOUTH, time.time()))
        assert lane.has_emergency() is True

    def test_density_tracking(self):
        lane = TrafficLane(direction=Direction.NORTH)
        for i in range(10):
            lane.add_vehicle(Vehicle(i, VehicleType.CAR, Direction.NORTH, time.time()))
        assert lane.density == 10
        lane.release_vehicle()
        assert lane.density == 9


class TestIntersection:
    def test_creation(self):
        inter = Intersection(intersection_id=1, name="Test")
        assert len(inter.lanes) == 4
        assert len(inter.signals) == 4
        assert inter.is_emergency_mode is False

    def test_all_directions_present(self):
        inter = Intersection(intersection_id=1, name="Test")
        for d in Direction:
            assert d in inter.lanes
            assert d in inter.signals


class TestAdaptiveTrafficController:
    def _make_controller(self):
        ctrl = AdaptiveTrafficController()
        inter = ctrl.add_intersection("Test Junction")
        return ctrl, inter

    def test_add_intersection(self):
        ctrl, inter = self._make_controller()
        assert len(ctrl.intersections) == 1
        assert inter.name == "Test Junction"

    def test_initial_signal_state(self):
        ctrl, inter = self._make_controller()
        assert inter.signals[Direction.NORTH].state == SignalState.GREEN
        assert inter.signals[Direction.SOUTH].state == SignalState.GREEN
        assert inter.signals[Direction.EAST].state == SignalState.RED
        assert inter.signals[Direction.WEST].state == SignalState.RED

    def test_adaptive_green_time_empty(self):
        ctrl, inter = self._make_controller()
        green = ctrl._compute_green_time(inter, Direction.NORTH)
        assert green == 10  # MIN_GREEN_TIME (empty queue)

    def test_adaptive_green_time_loaded(self):
        ctrl, inter = self._make_controller()
        for i in range(15):
            v = Vehicle(i, VehicleType.CAR, Direction.NORTH, time.time())
            inter.lanes[Direction.NORTH].add_vehicle(v)
        green = ctrl._compute_green_time(inter, Direction.NORTH)
        assert green == 60  # MAX_GREEN_TIME (at threshold)

    def test_adaptive_green_time_partial(self):
        ctrl, inter = self._make_controller()
        for i in range(7):
            v = Vehicle(i, VehicleType.CAR, Direction.EAST, time.time())
            inter.lanes[Direction.EAST].add_vehicle(v)
        green = ctrl._compute_green_time(inter, Direction.EAST)
        assert 10 < green < 60

    def test_complementary_pairs(self):
        assert AdaptiveTrafficController._is_complementary(Direction.NORTH, Direction.SOUTH)
        assert AdaptiveTrafficController._is_complementary(Direction.EAST, Direction.WEST)
        assert not AdaptiveTrafficController._is_complementary(Direction.NORTH, Direction.EAST)

    def test_emergency_detection(self):
        ctrl, inter = self._make_controller()
        v = Vehicle(1, VehicleType.EMERGENCY, Direction.EAST, time.time())
        inter.lanes[Direction.EAST].add_vehicle(v)
        ctrl._check_emergency(inter)
        assert inter.is_emergency_mode is True

    def test_log_entries(self):
        ctrl, inter = self._make_controller()
        assert len(ctrl.log) >= 1  # INIT log entry
        assert ctrl.log[0].event == "INIT"

    def test_get_statistics(self):
        ctrl, inter = self._make_controller()
        stats = ctrl.get_statistics(inter)
        assert "North" in stats
        assert "queue_length" in stats["North"]
        assert "signal" in stats["North"]

    def test_export_csv(self, tmp_path=None):
        ctrl, inter = self._make_controller()
        path = "/tmp/test_traffic_log.csv"
        ctrl.export_log_csv(path)
        assert os.path.exists(path)
        with open(path) as f:
            lines = f.readlines()
            assert len(lines) >= 2  # header + at least 1 entry
        os.remove(path)

    def test_start_stop(self):
        ctrl, inter = self._make_controller()
        ctrl.start()
        assert ctrl.running is True
        time.sleep(0.1)
        ctrl.stop()
        assert ctrl.running is False


if __name__ == "__main__":
    # Simple test runner without pytest
    import traceback
    test_classes = [TestVehicle, TestTrafficLane, TestIntersection, TestAdaptiveTrafficController]
    passed = 0
    failed = 0
    for cls in test_classes:
        instance = cls()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                try:
                    getattr(instance, method_name)()
                    print(f"  PASS  {cls.__name__}.{method_name}")
                    passed += 1
                except Exception as e:
                    print(f"  FAIL  {cls.__name__}.{method_name}: {e}")
                    traceback.print_exc()
                    failed += 1
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*50}")
