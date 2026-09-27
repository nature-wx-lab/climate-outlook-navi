import assert from "node:assert/strict";
import test from "node:test";
import { ClimateDataStore, todayInJapan } from "../data.js";

function fixture() {
  const dates = Array.from({ length: 29 }, (_, offset) => (
    new Date(Date.UTC(2026, 7, 29 + offset)).toISOString().slice(0, 10)
  ));
  const store = new ClimateDataStore();
  store.recentTemperature = {
    dates,
    normal_days: dates.map((value) => value.slice(5)),
    validation: { minimum_valid_ratio: 0.8 },
    stations: [{
      station_id: "17531", station_type: "amedas", name: "女満別",
      prefecture: "北海道", lat: 43.88, lon: 144.16, elevation_m: 33,
      observed_tenths: dates.map(() => 200),
      normal_tenths: dates.map(() => 150),
      normal_5day_tenths: dates.map(() => 150),
    }],
  };
  return store;
}

test("Japan's calendar day rolls over at 15:00 UTC", () => {
  assert.equal(todayInJapan(new Date("2026-09-26T14:59:59Z")), "2026-09-26");
  assert.equal(todayInJapan(new Date("2026-09-26T15:00:00Z")), "2026-09-27");
});

test("today's unavailable value is not fabricated or silently removed from the denominator", () => {
  const result = fixture().recentTemperaturePeriod("2026-08-29", "2026-09-27", "2026-09-27");
  assert.equal(result.expectedDays, 30);
  assert.equal(result.availableEnd, "2026-09-26");
  assert.equal(result.unavailableDays, 1);
  assert.equal(result.points[0].validDays, 29);
  assert.equal(result.points[0].expectedDays, 30);
  assert.equal(result.points[0].observedMean, 20);
  assert.equal(result.points[0].normalMean, 15);
});

test("an explicit historical period remains unchanged", () => {
  const result = fixture().recentTemperaturePeriod("2026-09-01", "2026-09-07", "2026-09-27");
  assert.equal(result.start, "2026-09-01");
  assert.equal(result.end, "2026-09-07");
  assert.equal(result.expectedDays, 7);
  assert.equal(result.unavailableDays, 0);
  assert.equal(result.points[0].anomaly, 5);
});

test("a five-day period with one gap uses the full official five-day normal", () => {
  const store = fixture();
  const station = store.recentTemperature.stations[0];
  const start = store.recentTemperature.dates.indexOf("2026-09-21");
  station.observed_tenths.splice(start, 5, 189, 160, 156, 149, null);
  station.normal_tenths.splice(start, 5, 151, 149, 146, 144, 142);
  station.normal_5day_tenths[start] = 146;
  const result = store.recentTemperaturePeriod("2026-09-21", "2026-09-25", "2026-09-27");
  assert.equal(result.points[0].normalMethod, "official_5day");
  assert.equal(result.points[0].validDays, 4);
  assert.equal(result.points[0].normalMean, 14.6);
  assert.ok(Math.abs(result.points[0].anomaly - 1.75) < 1e-10);
});

test("coverage below 80 percent produces no point", () => {
  const result = fixture().recentTemperaturePeriod("2026-09-23", "2026-09-29", "2026-09-29");
  assert.equal(result.expectedDays, 7);
  assert.equal(result.unavailableDays, 3);
  assert.equal(result.points.length, 0);
});

test("a wholly unavailable period reports no data", () => {
  const result = fixture().recentTemperaturePeriod("2026-09-27", "2026-09-27", "2026-09-27");
  assert.equal(result.availableEnd, null);
  assert.equal(result.unavailableDays, 1);
  assert.equal(result.points.length, 0);
});

test("future dates, invalid dates, and ranges over 93 days are rejected", () => {
  const store = fixture();
  assert.throws(() => store.recentTemperaturePeriod("2026-09-01", "2026-09-28", "2026-09-27"));
  assert.throws(() => store.recentTemperaturePeriod("2026-09-31", "2026-09-31", "2026-10-01"));
  assert.throws(
    () => store.recentTemperaturePeriod("2026-08-29", "2026-12-01", "2026-12-01"),
    /93日以内/,
  );
});
