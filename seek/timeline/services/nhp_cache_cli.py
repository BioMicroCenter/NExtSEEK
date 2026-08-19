# seek/timeline/services/nhp_cache_cli.py
# Command-line script, not a test module -- the previous name (nhp_cache_test.py)
# matched pytest's *_test.py collection pattern, so `pytest` imported it and its
# module-level logging.basicConfig wrote nhp_cache_test.log into the CWD.

import argparse
import logging
from ..services.nhp_service import get_nhp_data, fetch_NHP_PAV
from ..services.timeline_service import process_visits

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Capture detailed logs
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("nhp_cache_test.log"),  # Save logs to a file
        logging.StreamHandler()  # Print logs to the console
    ]
)

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Test NHP cache and data fetching logic.")
    parser.add_argument("nhp_name", type=str, help="The NHP name to fetch data for.")
    args = parser.parse_args() 

    # Fetch NHP data
    logging.info(f"Starting test for NHP name: {args.nhp_name}")
    data = get_nhp_data(args.nhp_name)
    # img_data = fetch_NHP_IMG(data)
    df = process_visits(data)
    # df = fetch_NHP_PAV(data)
    print(df)
    # data = save_nhp_info_to_json(args.nhp_name, filename="./app/api/data/NHP_info.json")
    if data:
        logging.info(f"Fetched data successfully for {args.nhp_name}.")
        print(f"Fetched {len(data)} records for NHP '{args.nhp_name}'")
    else:
        logging.warning(f"No data found for NHP '{args.nhp_name}'")
        print(f"No data found for NHP '{args.nhp_name}'")

if __name__ == "__main__":
    main()
