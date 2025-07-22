# SuperSexySteamDownloader

A powerful, console-based utility for downloading Steam game files using custom manifest data. This tool is designed for advanced users, archival purposes, and educational exploration of the Steam network.

---

## Disclaimer

**This software is intended for educational purposes only.** It interacts directly with Steam's CDN and network infrastructure. The author is not responsible for any misuse of this tool, including but not limited to the violation of Steam's Terms of Service or any form of software piracy. By using this software, you agree to assume all responsibility for your actions. **Use at your own risk.**

## Features

This application provides a robust set of features for managing and downloading Steam depot files:

-   **Download from SFD Files:** The core functionality revolves around a custom `.sfd` (Steam File Data) format, allowing you to download depots using pre-acquired manifest and key information.
-   **Robust Verification & Repair:** Automatically verifies local files against manifest hashes. If a file is corrupt or incomplete, the tool will truncate it to the last known-good byte and resume the download, saving bandwidth and time.
-   **Concurrent Downloads:** Utilizes a multi-threaded download engine to fetch multiple file chunks simultaneously, dramatically speeding up the download process.
-   **Secure Credential Storage:** Integrates with the native OS keyring (Windows Credential Manager, macOS Keychain, etc.) to securely save and reuse your Steam login credentials with your permission.
-   **Automatic Manifest Generation:** After a successful download, the tool automatically generates a correctly formatted `appmanifest_{appid}.acf` file, allowing for easier integration with the Steam client or other tools.
-   **Built-in Converters & Utilities:**
    -   **LUA/Manifest to SFD:** Convert older file formats from other tools into the modern `.sfd` format.
    -   **SFD Creator:** Create `.sfd` files from scratch by fetching live depot information from Steam (requires login).
    -   **AppID Lookup:** A handy tool to search for a game's name and find its corresponding Steam AppID.

## Understanding the `.sfd` File

The `.sfd` (Steam File Data) file is a custom, plain-text format created for this tool. It acts as a portable, self-contained "recipe" for downloading a specific set of Steam depots. It bundles all the necessary metadata that would normally be fetched from Steam's servers into a single file.

### File Structure and Multiple Depots

An `.sfd` file is structured to handle one or more depots for a single application.

1.  **AppID:** The first line is always the main AppID of the game or application.
2.  **Depot Blocks:** Following the AppID, the file contains a series of 4-line blocks. Each block represents a single depot. To include multiple depots, you simply **stack these 4-line blocks one after another** in the same file. The tool will read and queue each one in order.

Each 4-line block consists of:
-   **Line 1: Depot ID:** The ID of the depot.
-   **Line 2: Manifest GID:** The unique ID of the depot manifest to be used.
-   **Line 3: Depot Key:** The hexadecimal representation of the decryption key for this depot.
-   **Line 4: Manifest Content:** The string representation (`repr()`) of the raw, binary manifest content.

#### Example `.sfd` with Multiple Depots

Here is what a file might look like for downloading two depots for AppID `440`:
```
440
441
1234567890123456789
a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
b'...binary content representation...'
232251
9876543210987654321
f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5
b'...another binary content representation...'
EndOfFile
```

### Depot Priority and File Overwrites

It is possible for multiple depots to contain a file with the same name and path. This tool handles such conflicts with a simple "last one wins" rule.

When loading an `.sfd` file, the depots are processed in the order they appear. **If two depots contain the same file, the version from the depot that appears later in the file will overwrite the earlier one.**

After a download, a log file named `overwritten_files.txt` is created in the game's directory, detailing exactly which files were overwritten by which depots.

## Installation and Setup

### Prerequisites

-   Python 3.9 or higher.
-   `pip` (Python's package installer).

### Installation Steps

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/PSSGAMER/SuperSexySteamDownloader.git
    cd SuperSexySteamDownloader
    ```

2.  **Create and Activate a Virtual Environment (Recommended):**
    This keeps the project's dependencies isolated from your system.
    ```bash
    # Create the environment
    python -m venv venv

    # Activate it (on Windows PowerShell)
    .\venv\Scripts\Activate.ps1
    
    # Or on Linux/macOS
    source venv/bin/activate
    ```

3.  **Install Dependencies:**
    Install all required libraries. You can create a `requirements.txt` file or install them manually.
    ```bash
    pip install tqdm steam gevent-eventemitter keyring protobuf==3.20.3
    ```

### Running the Application

Once the setup is complete, you can run the application directly from the script:
```bash
python SuperSexySteamDownloader.py
```

## Planned Features

-   [ ] *Remember the 2FA login too*
-   [ ] *A better ACF generator*
-   [ ] *Interactive depot selection*
-   [ ] *A better exe Release*
-   [ ] *GUI Wrapper*

## License

This project is licensed under the **GNU Affero General Public License v3.0**. See the `LICENSE` file for the full text.
