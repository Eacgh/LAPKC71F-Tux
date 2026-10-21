import unittest

from nuc_center.ec import (
    PERF_MODE_BALANCE,
    PERF_MODE_OFFICE,
    PERF_MODE_TURBO,
    perf_mode_from_value,
    perf_mode_label,
)
from nuc_center.fan import DEFAULT_POINTS, FanPoint, interpolate, normalize_points, safe_percent
from nuc_center.sensors import BatteryInfo, SensorReader
from nuc_center.util import format_temp, parse_hex


class UtilTests(unittest.TestCase):
    def test_parse_hex(self):
        self.assertEqual(parse_hex("0x20"), 0x20)
        self.assertEqual(parse_hex("0x0000000000000020"), 0x20)
        self.assertIsNone(parse_hex("Error: AE_NOT_FOUND"))

    def test_temperature_conversion(self):
        self.assertEqual(format_temp(100, "C", 0), "100°C")
        self.assertEqual(format_temp(100, "F", 0), "212°F")
        self.assertEqual(format_temp(None, "C", 0), "--")


class EcDecodeTests(unittest.TestCase):
    def test_perf_modes(self):
        self.assertEqual(perf_mode_from_value(PERF_MODE_OFFICE), "quiet")
        self.assertEqual(perf_mode_from_value(PERF_MODE_BALANCE), "balanced")
        self.assertEqual(perf_mode_from_value(PERF_MODE_TURBO), "performance")
        self.assertEqual(perf_mode_from_value(0x00), "balanced")
        self.assertEqual(perf_mode_label("performance"), "Performance")


class FanCurveTests(unittest.TestCase):
    def test_interpolate(self):
        self.assertEqual(interpolate(DEFAULT_POINTS, 40), 20)
        self.assertEqual(interpolate(DEFAULT_POINTS, 60), 37)
        self.assertEqual(interpolate(DEFAULT_POINTS, 90), 100)

    def test_safety_floor(self):
        self.assertEqual(safe_percent(DEFAULT_POINTS, 90), 100)
        self.assertGreaterEqual(safe_percent([FanPoint(40, 10)], 80), 55)

    def test_normalize(self):
        points = normalize_points([(80, 50), (40, 20), (40, 30)])
        self.assertEqual([(p.temp, p.percent) for p in points], [(40, 30), (80, 50)])


class BatteryTests(unittest.TestCase):
    def test_health(self):
        bat = BatteryInfo(charge_full=5_825_000, charge_full_design=8_200_000)
        self.assertAlmostEqual(bat.health_percent, 71.036585, places=4)

    def test_energy(self):
        bat = BatteryInfo(charge_now=5_000_000, voltage=12.0)
        self.assertAlmostEqual(bat.energy_now, 60.0)


class SensorTests(unittest.TestCase):
    def test_live_read(self):
        reader = SensorReader()
        snap = reader.read()
        self.assertGreater(snap.cpu_load or 0, -1)
        self.assertIsNotNone(snap.mem_total)
        self.assertTrue(reader.device.product_name)


if __name__ == "__main__":
    unittest.main()
