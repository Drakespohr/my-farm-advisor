#!/usr/bin/env python3
"""Analyze weather data and create visualizations for 5 Nebraska fields."""

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
import numpy as np


def calculate_gdd(df: pd.DataFrame, base_temp: float = 10.0,
                  cap_temp: float = 30.0) -> pd.DataFrame:
    """Calculate daily and cumulative GDD per field."""
    out = df.copy()
    t_avg = ((out["T2M_MIN"] + out["T2M_MAX"]) / 2).clip(upper=cap_temp)
    out["gdd"] = (t_avg - base_temp).clip(lower=0)
    out["gdd_cumulative"] = out.groupby("field_id")["gdd"].cumsum()
    return out


def seasonal_summary(weather: pd.DataFrame) -> pd.DataFrame:
    """Calculate seasonal summaries for each field."""
    weather["month"] = weather["date"].dt.month
    weather["year"] = weather["date"].dt.year

    growing_season = weather[weather["month"].isin([4, 5, 6, 7, 8, 9])]

    summary = growing_season.groupby("field_id").agg({
        "T2M": ["mean", "min", "max"],
        "T2M_MAX": "max",
        "T2M_MIN": "min",
        "PRECTOTCORR": "sum",
        "ALLSKY_SFC_SW_DWN": "sum",
        "RH2M": "mean",
    }).round(2)

    summary.columns = ["_".join(col).strip() for col in summary.columns.values]
    summary = summary.reset_index()
    summary = summary.rename(columns={
        "T2M_mean": "avg_temp",
        "T2M_min": "min_temp",
        "T2M_max": "max_temp",
        "T2M_MAX_max": "highest_max_temp",
        "T2M_MIN_min": "lowest_min_temp",
        "PRECTOTCORR_sum": "precipitation_mm",
        "ALLSKY_SFC_SW_DWN_sum": "solar_radiation_mj_m2",
        "RH2M_mean": "avg_humidity_pct",
    })

    return summary


def plot_temperature_range(weather: pd.DataFrame, output_dir: str = "data"):
    """Plot temperature range for each field."""
    fig, axes = plt.subplots(3, 2, figsize=(14, 12))
    axes = axes.flatten()

    fields = weather["field_id"].unique()

    for idx, field_id in enumerate(fields):
        ax = axes[idx]
        field_data = weather[weather["field_id"] == field_id].sort_values("date")

        ax.fill_between(field_data["date"], field_data["T2M_MIN"], field_data["T2M_MAX"],
                       alpha=0.3, label="Min/Max range", color="steelblue")
        ax.plot(field_data["date"], field_data["T2M"], linewidth=0.8, label="Mean", color="navy")
        ax.axhline(0, color="grey", linewidth=0.5, linestyle="--")
        ax.axhline(10, color="green", linewidth=0.5, linestyle="--", alpha=0.5, label="GDD Base (10°C)")
        
        ax.set_ylabel("Temperature (°C)")
        ax.set_title(f"Daily Temperature — {field_id}")
        ax.legend(loc="upper right", fontsize=8)
        ax.xaxis.set_major_formatter(DateFormatter("%b"))

    axes[-1].axis("off")
    plt.tight_layout()
    plt.savefig(f"{output_dir}/temperature_all_fields.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved temperature_all_fields.png")


def plot_gdd_accumulation(weather_gdd: pd.DataFrame, output_dir: str = "data"):
    """Plot GDD accumulation curves for each field."""
    fig, ax = plt.subplots(figsize=(12, 6))

    fields = weather_gdd["field_id"].unique()
    colors = plt.cm.tab10(np.linspace(0, 1, len(fields)))

    for idx, field_id in enumerate(sorted(fields)):
        field_data = weather_gdd[weather_gdd["field_id"] == field_id].sort_values("date")
        ax.plot(field_data["date"], field_data["gdd_cumulative"], 
               label=field_id, color=colors[idx], linewidth=1.5)

    ax.set_ylabel("Cumulative GDD")
    ax.set_xlabel("Date")
    ax.set_title("Growing Degree Day Accumulation (2025) — Base 10°C, Cap 30°C")
    ax.legend()
    ax.xaxis.set_major_formatter(DateFormatter("%b"))
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(f"{output_dir}/gdd_accumulation.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved gdd_accumulation.png")


def plot_precipitation(weather: pd.DataFrame, output_dir: str = "data"):
    """Plot cumulative precipitation for each field."""
    fig, ax = plt.subplots(figsize=(12, 6))

    weather = weather.copy()
    weather["precip_cumulative"] = weather.groupby("field_id")["PRECTOTCORR"].cumsum()

    fields = weather["field_id"].unique()
    colors = plt.cm.tab10(np.linspace(0, 1, len(fields)))

    for idx, field_id in enumerate(sorted(fields)):
        field_data = weather[weather["field_id"] == field_id].sort_values("date")
        ax.plot(field_data["date"], field_data["precip_cumulative"], 
               label=field_id, color=colors[idx], linewidth=1.5)

    ax.set_ylabel("Cumulative Precipitation (mm)")
    ax.set_xlabel("Date")
    ax.set_title("Cumulative Precipitation (2025)")
    ax.legend()
    ax.xaxis.set_major_formatter(DateFormatter("%b"))
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(f"{output_dir}/precipitation_cumulative.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved precipitation_cumulative.png")


def plot_monthly_precipitation(weather: pd.DataFrame, output_dir: str = "data"):
    """Plot monthly precipitation bars."""
    weather = weather.copy()
    weather["month"] = weather["date"].dt.month
    weather["year"] = weather["date"].dt.year

    monthly = weather.groupby(["field_id", "month"])["PRECTOTCORR"].sum().reset_index()

    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    fig, ax = plt.subplots(figsize=(12, 6))

    x = np.arange(12)
    width = 0.15
    fields = sorted(monthly["field_id"].unique())
    colors = plt.cm.tab10(np.linspace(0, 1, len(fields)))

    for idx, field_id in enumerate(fields):
        field_monthly = monthly[monthly["field_id"] == field_id]
        precip = [field_monthly[field_monthly["month"] == m]["PRECTOTCORR"].values[0] 
                  if m in field_monthly["month"].values else 0 for m in range(1, 13)]
        ax.bar(x + idx * width, precip, width, label=field_id, color=colors[idx])

    ax.set_ylabel("Precipitation (mm)")
    ax.set_xlabel("Month")
    ax.set_title("Monthly Precipitation (2025)")
    ax.set_xticks(x + width * (len(fields) - 1) / 2)
    ax.set_xticklabels(months)
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    plt.savefig(f"{output_dir}/precipitation_monthly.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved precipitation_monthly.png")


def main():
    print("Loading weather data...")
    weather = pd.read_csv("data/weather_5fields_2025.csv", parse_dates=["date"])
    print(f"Loaded {len(weather)} records")

    print("\nCalculating GDD...")
    weather_gdd = calculate_gdd(weather, base_temp=10.0, cap_temp=30.0)

    final_gdd = weather_gdd.groupby("field_id")["gdd"].sum().reset_index()
    final_gdd.columns = ["field_id", "total_gdd"]
    print("\nTotal GDD by field:")
    print(final_gdd)

    print("\nCalculating seasonal summary...")
    summary = seasonal_summary(weather_gdd)
    print(summary.to_string(index=False))

    summary.to_csv("data/weather_stats.csv", index=False)
    print("\nSaved weather_stats.csv")

    print("\nCreating visualizations...")
    plot_temperature_range(weather)
    plot_gdd_accumulation(weather_gdd)
    plot_precipitation(weather)
    plot_monthly_precipitation(weather)

    print("\nDone!")


if __name__ == "__main__":
    main()
