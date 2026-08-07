# Run: python3 /home/ways_lab/repos/Tiny_Twin/channel/generate.py

def generate_data_file(filename="data_600000.txt", num_lines=600000):
    """
    Generates a file with 600,000 lines of "1 0.1 0.01 0.02".

    Args:
        filename (str): The name of the file to create.
        num_lines (int): The number of lines to generate.
    """
    try:
        with open(filename, "w") as file:
            for _ in range(num_lines):
                file.write("1 0.01 0.01 0.002 0.003 0.001 0.005 0.006 0.003 0.001 0.001 0.002 0.003 0.004 0.002 0.001 0.002 0.003 0.002 0.0001 0.001 0.001 0.001 0.002 0.003 0.001 0.005 0.006 0.003 0.001 0.001 0.002 0.003 0.004 0.002 0.001 0.002 0.003 0.002 0.0001 0.001 0.002 0.003 0.004 0.002 0.001 0.002 0.003 0.002 0.0001 0.001 0.01 0.01 0.002 0.003 0.001 0.005 0.006 0.003 0.001 0.001 0.002 0.003 0.004 0.002 0.001 0.002 0.003 0.002 0.0001 0.001 0.001 0.001 0.002 0.003 0.001 0.005 0.006 0.003 0.001 0.001 0.002 0.003 0.004 0.002 0.001 0.002 0.003 0.002 0.0001 0.001 0.002 0.003 0.004 0.002 0.001 0.002 0.003 0.002 0.0001\n")
        print(f"File '{filename}' generated successfully with {num_lines} lines.")
    except IOError as e:
        print(f"Error generating file: {e}")

def generate_seesaw(
    filename="/home/ways_lab/repos/Tiny_Twin/channel/channel_andrew.txt",
    tti_per_cycle=30000,
    num_cycles=4,
    index=1,
):
    """
    Generate a 10-tap channel file with one active tap.

    The active tap is selected with a 1-based column index. Within each cycle,
    that tap linearly decreases from 1.0 to 0.135519, then repeats for the next
    cycle. The output has tti_per_cycle * num_cycles rows.
    """
    if not 1 <= index <= 10:
        raise ValueError("index must be between 1 and 10, inclusive")
    if tti_per_cycle <= 0:
        raise ValueError("tti_per_cycle must be positive")
    if num_cycles <= 0:
        raise ValueError("num_cycles must be positive")

    start_value = 1.0
    end_value = 0.135519
    step = 0.0
    if tti_per_cycle > 1:
        step = (start_value - end_value) / (tti_per_cycle - 1)

    try:
        with open(filename, "w") as file:
            for _ in range(num_cycles):
                for tti in range(tti_per_cycle):
                    value = start_value - step * tti
                    taps = [0.0] * 10
                    taps[index - 1] = value
                    file.write(" ".join(f"{tap:.6f}" for tap in taps) + "\n")
        print(
            f"File '{filename}' generated successfully with "
            f"{tti_per_cycle * num_cycles} lines."
        )
    except IOError as e:
        print(f"Error generating file: {e}")


if __name__ == "__main__":
    generate_seesaw(index = 2)
