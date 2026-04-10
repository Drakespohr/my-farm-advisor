#!/usr/bin/env python3
"""
Soil Health & Sustainability Visualizations

1. Radar Chart: Top 5 vs Bottom 5 fields by OM%
2. Heatmap: All 20 fields across sustainability metrics
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path

# Data paths
DATA_DIR = Path("data")
OUTPUT_DIR = DATA_DIR

# Load data
df = pd.read_csv(DATA_DIR / "field_comparison_analysis.csv")
erosion_df = pd.read_csv(DATA_DIR / "erosion_risk_analysis.csv")

# Merge slope data
df = df.merge(erosion_df[['field_id', 'slope_pct']], on='field_id', how='left')


def normalize_metric(series, higher_is_better=True):
    """Normalize a metric to 0-1 scale."""
    min_val = series.min()
    max_val = series.max()
    if max_val == min_val:
        return pd.Series([0.5] * len(series))
    normalized = (series - min_val) / (max_val - min_val)
    if not higher_is_better:
        normalized = 1 - normalized
    return normalized


def calculate_sustainability_score(row):
    """Calculate overall sustainability score (0-1 scale)."""
    # Weights for each component
    om_score = row['om_norm'] * 0.25
    carbon_score = row['carbon_norm'] * 0.20
    ph_score = row['ph_norm'] * 0.15
    drainage_score = row['drainage_norm'] * 0.15
    erosion_score = row['k_norm'] * 0.15
    slope_score = row['slope_norm'] * 0.10
    return om_score + carbon_score + ph_score + drainage_score + erosion_score + slope_score


def prepare_data():
    """Prepare normalized data for visualizations."""
    # Normalize each metric
    df['om_norm'] = normalize_metric(df['om_pct'], higher_is_better=True)
    df['carbon_norm'] = normalize_metric(df['carbon_stock'], higher_is_better=True)
    df['ph_norm'] = normalize_metric(df['ph'], higher_is_better=True)  # Will adjust below
    
    # pH normalization - optimal is 6.5, penalize deviation
    df['ph_deviation'] = abs(df['ph'] - 6.5)
    df['ph_norm'] = 1 - normalize_metric(df['ph_deviation'], higher_is_better=True)
    
    # Drainage normalization
    drainage_map = {
        'Well drained': 1.0,
        'Moderately well drained': 0.75,
        'Somewhat poorly drained': 0.5,
        'Poorly drained': 0.25
    }
    df['drainage_norm'] = df['drainage_class'].map(drainage_map).fillna(0.5)
    
    # K-factor (erosion) - lower is better
    df['k_norm'] = normalize_metric(df['k_factor'], higher_is_better=False)
    
    # Slope - lower is better
    df['slope_norm'] = normalize_metric(df['slope_pct'], higher_is_better=False)
    
    # Calculate sustainability score
    df['sustainability_score'] = df.apply(calculate_sustainability_score, axis=1)
    
    # Sort by OM% (descending) for the tables
    df_sorted = df.sort_values('om_pct', ascending=False).reset_index(drop=True)
    
    return df_sorted


def create_radar_chart(df_sorted):
    """Create radar chart comparing Top 5 vs Bottom 5 fields."""
    print("\n[1/2] Creating radar chart...")
    
    # Get Top 5 and Bottom 5
    top5 = df_sorted.head(5)
    bottom5 = df_sorted.tail(5)
    
    # Metrics for radar chart
    categories = ['OM%', 'Carbon', 'pH', 'Drainage', 'Low Erosion']
    category_cols = ['om_norm', 'carbon_norm', 'ph_norm', 'drainage_norm', 'k_norm']
    
    # Create figure
    fig, ax = plt.subplots(figsize=(10, 10), subplot_kw=dict(projection='polar'))
    
    # Number of categories
    num_vars = len(categories)
    
    # Angle for each axis
    angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
    angles += angles[:1]  # Complete the loop
    
    # Plot Top 5 (unique colors)
    unique_colors = plt.cm.tab10(np.linspace(0, 1, 10))
    all_fields = pd.concat([top5, bottom5])
    
    for idx, (_, row) in enumerate(top5.iterrows()):
        values = [row[col] for col in category_cols]
        values += values[:1]  # Complete the loop
        ax.plot(angles, values, 'o-', linewidth=2.5, label=f'{row["field_id"]} (OM:{row["om_pct"]}%)',
               color=unique_colors[idx], alpha=0.85)
        ax.fill(angles, values, alpha=0.15, color=unique_colors[idx])
    
    # Plot Bottom 5 (unique colors - continue palette)
    for idx, (_, row) in enumerate(bottom5.iterrows()):
        values = [row[col] for col in category_cols]
        values += values[:1]  # Complete the loop
        ax.plot(angles, values, 's-', linewidth=2.5, label=f'{row["field_id"]} (OM:{row["om_pct"]}%)',
               color=unique_colors[idx + 5], alpha=0.85)
        ax.fill(angles, values, alpha=0.15, color=unique_colors[idx + 5])
    
    # Set category labels
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, fontsize=12)
    
    # Set radial limits
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(['25%', '50%', '75%', '100%'], fontsize=9)
    
    # Title and legend
    ax.set_title('Soil Health Comparison: Top 5 vs Bottom 5 Fields\n(by Organic Matter %)',
                fontsize=14, fontweight='bold', pad=20)
    
    ax.legend(loc='upper right', bbox_to_anchor=(1.35, 1.05), fontsize=9)
    
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'soil_health_radar.png', dpi=150, bbox_inches='tight',
               facecolor='white', edgecolor='none')
    print(f"  Saved: {OUTPUT_DIR / 'soil_health_radar.png'}")
    plt.close()


def create_heatmap(df_sorted):
    """Create sustainability heatmap for all 20 fields."""
    print("\n[2/2] Creating sustainability heatmap...")
    
    # Sort by sustainability score
    df_heat = df_sorted.sort_values('sustainability_score', ascending=False).reset_index(drop=True)
    
    # Define metrics and their column names
    metrics = {
        'OM%': 'om_pct',
        'Carbon': 'carbon_stock',
        'pH': 'ph',
        'Drainage': 'drainage_norm',
        'Erosion\nRisk': 'k_factor',
        'Slope': 'slope_pct',
        'Health\nScore': 'soil_health_score'
    }
    
    # Create color-coded matrix
    def get_color(value, col_name):
        """Determine color based on metric and value."""
        
        # Metrics where HIGHER is better
        if col_name in ['om_pct', 'carbon_stock', 'soil_health_score', 'drainage_norm']:
            if value >= 2.5 if col_name == 'om_pct' else (180 if col_name == 'carbon_stock' else 0.8):
                return '#27ae60'  # Green - Good
            elif value >= 1.8 if col_name == 'om_pct' else (120 if col_name == 'carbon_stock' else 0.6):
                return '#f1c40f'  # Yellow - Moderate
            else:
                return '#e74c3c'  # Red - Poor
        
        # pH - optimal between 6.0-7.0
        if col_name == 'ph':
            if 6.0 <= value <= 7.0:
                return '#27ae60'  # Green - Optimal
            elif 5.5 <= value < 6.0 or 7.0 < value <= 7.5:
                return '#f1c40f'  # Yellow - Acceptable
            else:
                return '#e74c3c'  # Red - Poor
        
        # Metrics where LOWER is better (K-factor, Slope)
        if col_name in ['k_factor', 'slope_pct']:
            if col_name == 'k_factor':
                if value <= 0.34:
                    return '#27ae60'  # Green - Low erosion
                elif value <= 0.38:
                    return '#f1c40f'  # Yellow - Moderate
                else:
                    return '#e74c3c'  # Red - High erosion
            else:  # slope_pct
                if value <= 3:
                    return '#27ae60'  # Green - Low slope
                elif value <= 6:
                    return '#f1c40f'  # Yellow - Moderate
                else:
                    return '#e74c3c'  # Red - Steep
        
        return '#95a5a6'  # Gray - Unknown
    
    # Build heatmap data
    field_ids = df_heat['field_id'].tolist()
    metric_names = list(metrics.keys())
    
    heatmap_data = []
    for metric_name in metric_names:
        col = metrics[metric_name]
        colors = []
        for _, row in df_heat.iterrows():
            value = row[col]
            color = get_color(value, col)
            colors.append(color)
        heatmap_data.append(colors)
    
    heatmap_data = np.array(heatmap_data)
    
    # Create figure
    fig, ax = plt.subplots(figsize=(14, 12))
    
    # Create heatmap
    for i, metric in enumerate(metric_names):
        for j, field in enumerate(field_ids):
            ax.add_patch(plt.Rectangle((j, i), 1, 1, 
                                        facecolor=heatmap_data[i, j],
                                        edgecolor='white', linewidth=1))
    
    # Set labels
    ax.set_xticks(np.arange(len(field_ids)) + 0.5)
    ax.set_xticklabels(field_ids, rotation=45, ha='right', fontsize=10)
    ax.set_yticks(np.arange(len(metric_names)) + 0.5)
    ax.set_yticklabels(metric_names, fontsize=11)
    
    ax.set_xlim(0, len(field_ids))
    ax.set_ylim(0, len(metric_names))
    
    ax.set_xlabel('Fields (sorted by Sustainability Score)', fontsize=12)
    ax.set_title('Sustainability Heatmap\nGreen = Good | Yellow = Moderate | Red = Poor',
                fontsize=14, fontweight='bold')
    
    # Add color legend
    legend_elements = [
        mpatches.Patch(facecolor='#27ae60', label='Good'),
        mpatches.Patch(facecolor='#f1c40f', label='Moderate'),
        mpatches.Patch(facecolor='#e74c3c', label='Poor')
    ]
    ax.legend(handles=legend_elements, loc='upper left', 
              title='Status', bbox_to_anchor=(1.02, 1))
    
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'sustainability_heatmap.png', dpi=150, bbox_inches='tight',
               facecolor='white', edgecolor='none')
    print(f"  Saved: {OUTPUT_DIR / 'sustainability_heatmap.png'}")
    plt.close()


def main():
    print("=" * 60)
    print("SOIL HEALTH & SUSTAINABILITY VISUALIZATIONS")
    print("=" * 60)
    
    # Prepare data
    print("\n[Prep] Preparing data...")
    df_sorted = prepare_data()
    
    # Create visualizations
    create_radar_chart(df_sorted)
    create_heatmap(df_sorted)
    
    print("\n" + "=" * 60)
    print("VISUALIZATIONS COMPLETE")
    print("=" * 60)
    print(f"\nOutput files:")
    print(f"  - {OUTPUT_DIR / 'soil_health_radar.png'}")
    print(f"  - {OUTPUT_DIR / 'sustainability_heatmap.png'}")


if __name__ == "__main__":
    main()
