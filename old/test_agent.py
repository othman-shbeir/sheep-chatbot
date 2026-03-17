import os
import sys

# Add project path so we can import agent
sys.path.append(r"d:\Sheep Project")

from services.agent import run_agent


def main():
    try:
        print("Testing IN_DOMAIN query...")
        res = run_agent("What should I feed my sheep in winter?")
        print("Response:", res)
        print("-" * 50)

        print("Testing OUT_OF_DOMAIN query...")
        res = run_agent("Can you write a python script to scrape a website?")
        print("Response:", res)
        print("-" * 50)

        print("Testing tool query...")
        res = run_agent("Search the web for the latest price of sheep wool in 2024")
        print("Response:", res)

    except Exception as e:
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
