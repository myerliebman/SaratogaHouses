import re
import csv

def extract_addresses(input_file='data.txt', output_file='addresses.csv'):
    """
    Extracts addresses from a given text file and saves them to a CSV file.

    This function reads an input text file, searches for strings enclosed in
    double quotes that contain at least one digit, and writes these strings
    (assumed to be addresses) to a specified CSV file.

    Args:
        input_file (str): The name of the text file to read from.
        output_file (str): The name of the CSV file to write the addresses to.
    """
    # This regular expression finds any text enclosed in double quotes.
    # The parentheses ( ) create a "capturing group" for the content inside the quotes.
    address_pattern = re.compile(r'"([^"]*)"')
    
    # A list to hold the addresses we find.
    found_addresses = []

    print(f"Attempting to read from '{input_file}'...")

    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            for line in f:
                # Search for the pattern in the current line.
                match = address_pattern.search(line)
                if match:
                    # match.group(0) would be the full match with quotes
                    # match.group(1) is just the content of the first capturing group
                    address = match.group(1)
                    
                    # Check if the extracted address contains any numbers.
                    # The any() function is efficient for this check.
                    if any(char.isdigit() for char in address):
                        found_addresses.append(address)
                        print(f"  Found valid address: {address}")
                    else:
                        print(f"  Skipping non-address line: {address}")


    except FileNotFoundError:
        print(f"Error: The file '{input_file}' was not found.")
        print("Please make sure the file exists in the same directory as the script.")
        return
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return

    if not found_addresses:
        print("No valid addresses were found in the file.")
        return

    # Write the collected addresses to a CSV file.
    try:
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            # Create a writer object to write to the CSV.
            writer = csv.writer(csvfile)
            
            # Write a header row.
            writer.writerow(['Address'])
            
            # Write each address as a new row.
            for address in found_addresses:
                writer.writerow([address])
        
        print(f"\nSuccessfully extracted {len(found_addresses)} addresses to '{output_file}'.")

    except Exception as e:
        print(f"An error occurred while writing to the CSV file: {e}")


if __name__ == '__main__':
    # To run the script, save your data to a file named 'data.txt'
    # in the same directory, and then execute this Python script.
    extract_addresses()
