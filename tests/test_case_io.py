import csv
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from roster.case_io import CaseError, GROUPS, load_case, validate_case


TEST_DIR = ROOT / "test"
CASE_NAMES = (
    "case_01_balanced_20d",
    "case_02_tight_28d",
    "case_03_overloaded_20d",
    "case_04_small_7d",
)
BASE_CASE = TEST_DIR / "case_01_balanced_20d"


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def write_csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


class CaseIoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.case_dir = Path(self._tmp.name) / "case"
        shutil.copytree(BASE_CASE, self.case_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def errors_for_copy(self):
        with self.assertRaises(CaseError) as caught:
            load_case(str(self.case_dir))
        return caught.exception.errors

    def assert_error_contains(self, errors, needle):
        self.assertTrue(
            any(needle in error for error in errors),
            f"{needle!r} not found in {errors!r}",
        )

    def test_all_sample_cases_load_clean(self):
        for name in CASE_NAMES:
            with self.subTest(case=name):
                case = load_case(str(TEST_DIR / name))
                self.assertEqual(validate_case(case), [])
                self.assertEqual(set(case.by_group), set(GROUPS))

    def test_missing_file(self):
        (self.case_dir / "demand.csv").unlink()
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "demand.csv: file missing")

    def test_wrong_header(self):
        path = self.case_dir / "shifts.csv"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("shift_id", "shift", 1), encoding="utf-8")
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "shifts.csv: wrong header")

    def test_duplicate_roll_no(self):
        path = self.case_dir / "students.csv"
        header, rows = read_csv(path)
        rows.append(dict(rows[0]))
        write_csv(path, header, rows)
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "duplicate roll_no")

    def test_duplicate_shift_id(self):
        path = self.case_dir / "shifts.csv"
        header, rows = read_csv(path)
        rows.append(dict(rows[0]))
        write_csv(path, header, rows)
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "duplicate shift_id")

    def test_unknown_group(self):
        path = self.case_dir / "students.csv"
        header, rows = read_csv(path)
        rows[0]["group"] = "UNKNOWN"
        write_csv(path, header, rows)
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "unknown group")

    def test_unknown_shift_id(self):
        path = self.case_dir / "demand.csv"
        header, rows = read_csv(path)
        rows[0]["shift_id"] = "X"
        write_csv(path, header, rows)
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "unknown shift_id")

    def test_horizon_outside_range(self):
        path = self.case_dir / "config.csv"
        header, rows = read_csv(path)
        for row in rows:
            if row["key"] == "horizon_days":
                row["value"] = "29"
        write_csv(path, header, rows)
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "horizon_days 29 outside 1..28")

    def test_incomplete_demand_grid(self):
        path = self.case_dir / "demand.csv"
        header, rows = read_csv(path)
        write_csv(path, header, rows[1:])
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "demand grid incomplete")

    def test_negative_demand(self):
        path = self.case_dir / "demand.csv"
        header, rows = read_csv(path)
        rows[0]["required"] = "-1"
        write_csv(path, header, rows)
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "negative required")

    def test_demand_above_group_size(self):
        path = self.case_dir / "demand.csv"
        header, rows = read_csv(path)
        group = rows[0]["group"]
        _, student_rows = read_csv(self.case_dir / "students.csv")
        group_size = sum(1 for student in student_rows if student["group"] == group)
        rows[0]["required"] = str(group_size + 1)
        write_csv(path, header, rows)
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "exceeds group")

    def test_preference_day_outside_horizon(self):
        path = self.case_dir / "preferences.csv"
        header, rows = read_csv(path)
        target = next(row for row in rows if row["type"] == "OFF_DAY")
        target["day"] = "21"
        write_csv(path, header, rows)
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "OFF_DAY day 21 outside 1..20")

    def test_preference_weight_outside_range(self):
        path = self.case_dir / "preferences.csv"
        header, rows = read_csv(path)
        rows[0]["weight"] = "6"
        write_csv(path, header, rows)
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "weight 6 outside 1..5")

    def test_shift_hours_mismatch(self):
        path = self.case_dir / "shifts.csv"
        header, rows = read_csv(path)
        for row in rows:
            if row["shift_id"] == "N":
                row["hours"] = "3"
        write_csv(path, header, rows)
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "shift 'N' hours mismatch")

    def test_crosses_midnight_mismatch(self):
        path = self.case_dir / "shifts.csv"
        header, rows = read_csv(path)
        for row in rows:
            if row["shift_id"] == "N":
                row["crosses_midnight"] = "no"
        write_csv(path, header, rows)
        errors = self.errors_for_copy()
        self.assert_error_contains(errors, "crosses_midnight")


if __name__ == "__main__":
    unittest.main()
