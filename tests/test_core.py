import unittest

from nuc_center.ec import (
    EC_FAN_CPU_DUTY,
    EC_FAN_CPU_UP,
    EC_FAN_GPU_DUTY,
    EC_GPU_CTGP,
    EC_GPU_DB_CAP,
    EC_GPU_DB_WATT,
    EC_GPU_TGP_CONTROL,
    GPU_POWER_MODES,
    POWER_PROFILES,
    NUC_PERF_MODE_VALUES,
    PERF_MODE_BALANCE,
    PERF_MODE_OFFICE,
    PERF_MODE_TURBO,
    expand_fan_curve,
    fan_table_writes,
    gpu_power_registers,
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


class GpuPowerTests(unittest.TestCase):
    # Values read back from NUC Studio on a LAPKC71F.
    def test_balanced_100w(self):
        writes = gpu_power_registers(100, 0x02)
        self.assertEqual(writes[EC_GPU_TGP_CONTROL], 0x03)
        self.assertEqual(writes[EC_GPU_CTGP], 0)
        self.assertEqual(writes[EC_GPU_DB_CAP], 45)
        self.assertEqual(writes[EC_GPU_DB_WATT], 20)

    def test_performance_125w(self):
        writes = gpu_power_registers(125, 0x03)
        self.assertEqual(writes[EC_GPU_TGP_CONTROL], 0x07)
        self.assertEqual(writes[EC_GPU_CTGP], 25)
        self.assertEqual(writes[EC_GPU_DB_WATT], 20)

    def test_battery_80w_clears_bits(self):
        writes = gpu_power_registers(80, 0x07)
        self.assertEqual(writes[EC_GPU_TGP_CONTROL], 0x01)
        self.assertEqual(writes[EC_GPU_CTGP], 0)
        self.assertEqual(writes[EC_GPU_DB_CAP], 0)
        self.assertEqual(writes[EC_GPU_DB_WATT], 0)

    def test_unrelated_bits_preserved(self):
        self.assertEqual(gpu_power_registers(80, 0x50)[EC_GPU_TGP_CONTROL], 0x51)
        self.assertEqual(gpu_power_registers(100, 0x50)[EC_GPU_TGP_CONTROL], 0x53)

    def test_unknown_wattage_rejected(self):
        with self.assertRaises(ValueError):
            gpu_power_registers(90, 0x03)
        self.assertNotIn(90, GPU_POWER_MODES)

    def test_nuc_fan_mode_values(self):
        # 0x10 is Turbo/Performance; the old table mapped it to "quiet".
        self.assertEqual(NUC_PERF_MODE_VALUES, {"balanced": 0x00, "performance": 0x10})


class PowerProfileTests(unittest.TestCase):
    def test_profiles_match_ec_defaults(self):
        self.assertEqual(POWER_PROFILES["balanced"]["pl1"], 45)
        self.assertEqual(POWER_PROFILES["balanced"]["pl2"], 65)
        self.assertEqual(POWER_PROFILES["quiet"]["pl1"], 30)
        self.assertEqual(POWER_PROFILES["quiet"]["pl2"], 60)
        self.assertEqual(POWER_PROFILES["performance"]["pl1"], 65)
        self.assertEqual(POWER_PROFILES["performance"]["pl2"], 90)
        for profile in POWER_PROFILES.values():
            self.assertIn(profile["gpu_power"], GPU_POWER_MODES)


class FanTableTests(unittest.TestCase):
    POINTS = [(40, 50), (50, 60), (60, 75), (70, 90), (80, 100)]

    def test_expand_to_15_slots(self):
        curve = expand_fan_curve(self.POINTS)
        self.assertEqual(len(curve), 15)
        self.assertEqual(curve[0], (40, 50))
        self.assertEqual(curve[-1], (80, 100))
        self.assertEqual([temp for temp, _ in curve], sorted(temp for temp, _ in curve))
        self.assertEqual([pct for _, pct in curve], sorted(pct for _, pct in curve))

    def test_table_writes(self):
        writes = dict(fan_table_writes(self.POINTS))
        self.assertEqual(writes[EC_FAN_CPU_UP], 20)
        self.assertEqual(writes[EC_FAN_CPU_DUTY], 0)
        self.assertEqual(writes[EC_FAN_CPU_UP + 1], 40)
        self.assertEqual(writes[EC_FAN_CPU_DUTY + 1], 100)   # 50 % * 2
        self.assertEqual(writes[EC_FAN_CPU_DUTY + 15], 200)  # 100 % * 2
        self.assertEqual(writes[EC_FAN_GPU_DUTY + 8], writes[EC_FAN_CPU_DUTY + 8])


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
