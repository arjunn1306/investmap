import pandas as pd
import sys

def update_rent(file_path, monthly_ratio=0.007):
    try:
        df = pd.read_csv(file_path)
        # 0.007 = 0.7% of purchase price as monthly rent
        df["est_rent"] = df["price"] * monthly_ratio
        
        output_name = file_path.replace(".csv", "_adj_rent.csv")
        df.to_csv(output_name, index=False)
        print(f"Updated {len(df)} rows. New file: {output_name}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # Usage: python add_rent.py redfin_47906.csv
    if len(sys.argv) > 1:
        update_rent(sys.argv[1])
    else:
        print("Please provide the CSV filename.")