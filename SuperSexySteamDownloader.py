import os
import time
import ast
import getpass
import re
import concurrent.futures
import hashlib
import sys
import keyring
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional

# steam.py imports
from steam.client import SteamClient
from steam.client.cdn import CDNClient
from steam.enums import EResult
from steam.exceptions import ManifestError
from tqdm import tqdm

# This compatibility check is needed for type hinting tqdm in older Python versions.
if sys.version_info < (3, 9):
    from typing import cast
    TqdmType = cast(Any, tqdm)
else:
    TqdmType = tqdm

# Constants for Keyring service to avoid magic strings
KEYRING_SERVICE_NAME = "SteamDownloaderApp"
KEYRING_USERNAME_KEY = "steam_username"

class SteamManifestGenerator:
    """
    A tool to generate modern, cleanly formatted Steam appmanifest.acf files.
    This can be run as part of the main app or standalone.
    """
    def __init__(self, app_id: int, output_dir: str = ".", client: Optional[SteamClient] = None):
        self.app_id = app_id
        self.output_dir = output_dir
        self.app_info: Dict[str, Any] = {}
        self.depots: Dict[int, Any] = {}
        self.shared_depots: Dict[int, int] = {}
        
        # This allows the generator to use an existing, logged-in client.
        if client:
            self.client = client
            self._was_client_passed = True
        else:
            self.client = SteamClient()
            self._was_client_passed = False

    def connect_to_steam(self) -> bool:
        """Establishes a connection to Steam, using an existing one if available."""
        if self.client.logged_on:
            print("Using existing Steam connection.")
            return True

        print("Attempting to log in to Steam anonymously...")
        result = self.client.anonymous_login()
        if result != EResult.OK:
            print(f"Failed to login anonymously: {result!r}")
            return False
        
        print("Successfully logged in anonymously.")
        return True

    def get_product_info(self) -> Optional[Dict[str, Any]]:
        """Fetches product info for the main app_id."""
        print(f"Fetching product info for app_id: {self.app_id}...")
        try:
            res = self.client.get_product_info(apps=[self.app_id])
            if 'apps' not in res or self.app_id not in res['apps']:
                print(f"Error: No product info returned for app_id {self.app_id}. The app might not exist.")
                return None
            return res['apps'][self.app_id]
        except Exception as e:
            print(f"An error occurred while fetching product info: {e}")
            return None

    def parse_app_data(self) -> bool:
        """Parses the main app data, including all depots (regular, DLC, and shared)."""
        app_data = self.get_product_info()
        if not app_data:
            print(f"Could not retrieve data for main app {self.app_id}. Exiting.")
            return False
            
        if 'common' not in app_data:
            print(f"Error: App {self.app_id} seems to be invalid or has no 'common' section.")
            return False

        common = app_data.get('common', {})
        self.app_info['name'] = common.get('name', f'Unknown App {self.app_id}')
        
        config = app_data.get('config', {})
        self.app_info['installdir'] = config.get('installdir', re.sub(r'[<>:"/\\|?*]', '_', self.app_info['name']))

        depots_data = app_data.get('depots', {})
        self.app_info['buildid'] = depots_data.get('branches', {}).get('public', {}).get('buildid', '0')

        for depot_id_str, depot_info in depots_data.items():
            if not depot_id_str.isdigit(): continue

            if depot_info.get('sharedinstall') == '1':
                parent_app = depot_info.get('depotfromapp', depot_id_str)
                self.shared_depots[int(depot_id_str)] = int(parent_app)
                continue

            public_manifest_data = depot_info.get('manifests', {}).get('public')
            if not public_manifest_data or 'gid' not in public_manifest_data:
                continue

            details = {
                'manifest': public_manifest_data['gid'],
                'size': int(public_manifest_data.get('size', '0'))
            }
            if 'dlcappid' in depot_info:
                details['dlc_appid'] = int(depot_info['dlcappid'])
            self.depots[int(depot_id_str)] = details

        print(f"Finished parsing. Found {len(self.depots)} installable depots and {len(self.shared_depots)} shared depots.")
        return True

    def generate_acf_content(self) -> str:
        """Generates the ACF content with precise, manual formatting."""
        print("Generating ACF file content...")
        size_on_disk = sum(d['size'] for d in self.depots.values())
        
        t1, t2, t3 = "\t", "\t\t", "\t\t\t"
        content_parts = ['"AppState"\n', '{\n']
        main_kv = {
            "appid": self.app_id, "Universe": 1, "LauncherPath": "", "name": self.app_info['name'],
            "StateFlags": 4, "installdir": self.app_info['installdir'], "LastUpdated": 0,
            "SizeOnDisk": size_on_disk, "StagingSize": 0, "buildid": self.app_info['buildid'],
            "LastOwner": "None", "UpdateResult": 0, "BytesToDownload": 0, "BytesDownloaded": 0,
            "BytesToStage": 0, "BytesStaged": 0, "TargetBuildID": 0, "AutoUpdateBehavior": 0,
            "AllowOtherDownloadsWhileRunning": 0, "ScheduledAutoUpdate": 0
        }
        for key, value in main_kv.items():
            content_parts.append(f'{t1}"{key}"{t2}"{value}"\n')

        content_parts.extend([f'{t1}"InstalledDepots"\n', f'{t1}{{\n'])
        for depot_id, details in sorted(self.depots.items()):
            content_parts.extend([
                f'{t2}"{depot_id}"\n', f'{t2}{{\n', f'{t3}"manifest"{t2}"{details["manifest"]}"\n',
                f'{t3}"size"{t2}"{details["size"]}"\n'
            ])
            if 'dlc_appid' in details:
                content_parts.append(f'{t3}"dlcappid"{t2}"{details["dlc_appid"]}"\n')
            content_parts.append(f'{t2}}}\n')
        content_parts.append(f'{t1}}}\n')

        content_parts.extend([f'{t1}"SharedDepots"\n', f'{t1}{{\n'])
        for depot_id, parent_id in sorted(self.shared_depots.items()):
            content_parts.append(f'{t2}"{depot_id}"{t2}"{parent_id}"\n')
        content_parts.append(f'{t1}}}\n')

        content_parts.append('}\n')
        return "".join(content_parts)

    def write_acf_file(self, content: str) -> None:
        """Writes the generated content to the final .acf file."""
        output_path = Path(self.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        file_path = output_path / f"appmanifest_{self.app_id}.acf"
        
        try:
            file_path.write_text(content, encoding="utf-8")
            print("-" * 50)
            print("Successfully generated manifest file!")
            print(f"Location: {file_path.resolve()}")
            print("-" * 50)
        except IOError as e:
            print(f"Error writing file: {e}")

    def run(self) -> None:
        """Orchestrates the entire generation process."""
        if self.connect_to_steam():
            if self.parse_app_data():
                acf_content = self.generate_acf_content()
                self.write_acf_file(acf_content)

        # Only log out if this instance created its own client
        if not self._was_client_passed:
            self.client.logout()
        print("Manifest generator finished.")


class SteamDownloaderApp:
    """A console application for downloading Steam game files using custom data files."""

    def __init__(self) -> None:
        """Initializes the application's state and clients."""
        self.client: SteamClient = SteamClient()
        self.cdn: CDNClient = CDNClient(self.client)
        self.sfd_path: Optional[Path] = None
        self.lua_path: Optional[Path] = None
        self.app_id: Optional[int] = None
        self.depots_to_download: List[Dict[str, Any]] = []
        self.overwrite_log: List[str] = []
        # This determines how many files are downloaded simultaneously.
        self.max_workers: int = 10
        # This cache avoids looking up the same AppID multiple times per session.
        self.app_name_cache: Dict[int, str] = {}

    def _clear_screen(self) -> None:
        os.system('cls' if os.name == 'nt' else 'clear')

    def _reset_queue(self) -> None:
        """Resets the application state to prepare for a new download queue."""
        self.app_id = None
        self.depots_to_download = []
        self.overwrite_log = []
        # Clear cached data in the CDN client to prevent state from a previous .sfd file
        # from "leaking" into the new session.
        self.cdn.manifests.clear()
        self.cdn.depot_keys.clear()
        print("Download queue has been cleared.")

    def _sanitize_filename(self, name: str) -> str:
        """Removes characters from a string that are invalid in folder/file names."""
        return re.sub(r'[<>:"/\\|?*]', '_', name)

    def _get_game_name(self, app_id: int) -> str:
        """Fetches a game's name from its AppID, using a cache for performance."""
        if app_id in self.app_name_cache:
            return self.app_name_cache[app_id]
        if not self._ensure_logged_in():
            print("Cannot fetch game name without login.")
            return str(app_id)

        print(f"Fetching product info for AppID {app_id}...")
        try:
            resp = self.client.get_product_info([app_id])
            game_name: str = resp['apps'][app_id]['common']['name']
            self.app_name_cache[app_id] = game_name
            return game_name
        except (KeyError, Exception) as e:
            print(f"Could not fetch game name. Using AppID as fallback. Error: {e}")
            return str(app_id)

    def _ensure_logged_in(self) -> bool:
        """Checks for login, attempts anonymous if needed. Returns True on success."""
        if self.client.logged_on:
            return True
        print("\nNot logged in. Attempting auto anonymous login...")
        try:
            self.client.anonymous_login()
            if self.client.logged_on:
                print("Anonymous login successful.")
                return True
            else:
                print("Auto login failed. Cannot proceed.")
                return False
        except Exception as e:
            print(f"An error occurred during login: {e}")
            return False

    def _write_sfd_file(self, filename: Path, app_id: int, collected_depots: List[Dict[str, Any]]) -> None:
        """A centralized helper to write collected depot data to an .sfd file."""
        with filename.open('w', errors='ignore') as f:
            f.write(f'{app_id}\n')
            for depot in collected_depots:
                f.write(f"{depot['depot_id']}\n")
                f.write(f"{depot['manifest_id']}\n")
                f.write(f"{depot['depot_key'].hex()}\n")
                f.write(f"{repr(depot['manifest_content'])}\n")
            f.write("EndOfFile\n")
        print(f"\nSuccessfully created {filename.name}.")
    
    def app_id_lookup_tool(self) -> None:
        """A utility to search the Steam store for a game name and list corresponding AppIDs."""
        search_term = input("Enter a game name to search for: ")
        if not search_term: return
        print(f"Searching for '{search_term}'...")
        try:
            # Use a Session object to persist cookies.
            with requests.Session() as s:
                s.get("https://store.steampowered.com")
                api_url = "https://store.steampowered.com/api/storesearch/"
                params = {'term': search_term, 'l': 'english', 'cc': 'US', 'count': 20}
                response = s.get(api_url, params=params)
                response.raise_for_status()
                data = response.json()
                if not data.get('items'):
                    print("No results found."); return
                print("\n--- Search Results ---")
                for i, item in enumerate(data['items']):
                    app_id = item.get('id', 'N/A')
                    name = item.get('name', 'Unknown')
                    print(f"  {i+1}. {name} (AppID: {app_id})")
                print("----------------------")
        except requests.exceptions.RequestException as e:
            print(f"An error occurred while searching: {e}")

    def login(self) -> None:
        """Handles the manual login process, with optional Keyring integration."""
        try:
            anon_choice = input("Anonymous Login? (Y/N): ").upper()
            if anon_choice == 'N':
                saved_username = keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME_KEY)
                if saved_username:
                    if input(f"Use saved credentials for user '{saved_username}'? (Y/N): ").upper() == 'Y':
                        password = keyring.get_password(KEYRING_SERVICE_NAME, saved_username)
                        if password:
                            print("Attempting login with saved credentials...")
                            self.client.cli_login(saved_username, password)
                
                if not self.client.logged_on:
                    username = input('Username: ')
                    password = getpass.getpass('Password (Text is invisible): ')
                    self.client.cli_login(username, password)

                    if self.client.logged_on:
                        if input("Save credentials for next time? (Y/N): ").upper() == 'Y':
                            keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME_KEY, username)
                            keyring.set_password(KEYRING_SERVICE_NAME, username, password)
                            print("Credentials saved securely in your OS keyring.")
            else:
                self.client.anonymous_login()

            print('Login successful.' if self.client.logged_on else 'Login failed.')
        except Exception as e:
            print(f"An error occurred during login: {e}")
        
        input("Press Enter to continue...")
        
    def _select_file_from_list(self, extension: str) -> Optional[Path]:
        """A generic helper to find files and return the user's selection as a Path object."""
        # This logic is crucial for PyInstaller compatibility.
        # It determines the correct base directory whether running as a script or a frozen exe.
        if getattr(sys, 'frozen', False):
            # If the application is run as a bundle, the base path is the exe's directory.
            base_dir = Path(sys.executable).parent
        else:
            # If run as a script, the base path is the script's directory.
            base_dir = Path(__file__).parent

        print(f"Searching for {extension} files in and around: {base_dir.resolve()}")
        found_files = sorted(list(base_dir.rglob(f"*{extension}")))

        if not found_files:
            print(f"No {extension} files found."); return None
        
        print(f"Found {extension} files:")
        for i, f in enumerate(found_files):
            # To make the output cleaner, show the path relative to the search directory.
            try:
                relative_path = f.relative_to(base_dir)
                print(f"  {i+1}. {relative_path}")
            except ValueError:
                print(f"  {i+1}. {f}")

        try:
            choice = int(input(f"Select a {extension} file (number), or 0 to cancel: "))
            if 1 <= choice <= len(found_files):
                return found_files[choice - 1]
            elif choice != 0:
                print("Invalid selection.")
        except ValueError:
            print("Invalid input.")
        return None
        
    def _load_sfd_from_path(self, sfd_path: Path) -> None:
        """The core engine for parsing an SFD file and populating the download queue."""
        self._reset_queue()
        print(f"Loading data from {sfd_path.name}...")
        try:
            with sfd_path.open('r', errors='ignore') as f:
                self.app_id = int(f.readline().strip())
                while True:
                    line1 = f.readline()
                    if not line1 or line1.strip() == "EndOfFile": break
                    line2, line3, line4 = f.readline(), f.readline(), f.readline()
                    if not (line2 and line3 and line4):
                        print("\nWarning: .sfd file is incomplete."); break
                    
                    depot_id = int(line1.strip())
                    manifest_id = int(line2.strip())
                    depot_key = bytes.fromhex(line3.strip())
                    manifest_content = ast.literal_eval(line4.strip())

                    depot_info = {'depot_id': depot_id, 'manifest_id': manifest_id, 'depot_key': depot_key, 'manifest_content': manifest_content}
                    self.depots_to_download.append(depot_info)
                    
                    self.cdn.depot_keys[depot_id] = depot_info['depot_key']
                    self.cdn.manifests[(self.app_id, depot_id, manifest_id)] = self.cdn.DepotManifestClass(self.cdn, self.app_id, depot_info['manifest_content'])
        except Exception as e:
            print(f"A critical error occurred while loading: {e}"); self._reset_queue()
        
        print("SFD data loaded successfully." if self.depots_to_download else "No depots were loaded.")

    def load_sfd_workflow(self) -> None:
        """A user-facing workflow that finds and then loads an SFD file."""
        sfd_path = self._select_file_from_list('.sfd')
        if not sfd_path:
            print("SFD loading cancelled."); return
        self.sfd_path = sfd_path
        self._load_sfd_from_path(sfd_path)
        
    def convert_lua_workflow(self) -> None:
        """A user-facing workflow that converts LUA/manifest files into a new SFD file."""
        lua_path = self._select_file_from_list('.lua')
        if not lua_path:
            print("LUA conversion cancelled."); return
        self.lua_path = lua_path
        
        try:
            app_id = int(input("Please enter the main AppID for this game: "))
        except ValueError:
            print("Invalid AppID."); return
        
        game_name = self._get_game_name(app_id)
        print(f"Processing {self.lua_path.name} for '{game_name}' (AppID {app_id})...")
        
        parsed_depots: Dict[int, Dict[str, str]] = {}
        try:
            with self.lua_path.open('r') as f:
                re_addappid = re.compile(r'addappid\(\s*(\d+)\s*,\s*\d+\s*,\s*"([a-fA-F0-9]+)"\)')
                re_setmanifest = re.compile(r'setManifestid\(\s*(\d+)\s*,\s*"(\d+)"')
                for line in f:
                    match_add = re_addappid.search(line)
                    if match_add:
                        depot_id, depot_key = match_add.groups()
                        parsed_depots[int(depot_id)] = {'key': depot_key}
                        continue
                    match_set = re_setmanifest.search(line)
                    if match_set:
                        depot_id, manifest_id = match_set.groups()
                        if int(depot_id) in parsed_depots: parsed_depots[int(depot_id)]['manifest_id'] = manifest_id
        except Exception as e: print(f"Error parsing .lua file: {e}"); return
        
        if not parsed_depots: print("No valid depots were parsed."); return
        print(f"Found {len(parsed_depots)} depots. Searching for .manifest files...")
        
        lua_dir = self.lua_path.parent
        collected_depots = []
        for depot_id, data in parsed_depots.items():
            if 'manifest_id' not in data: continue
            
            manifest_path = lua_dir / f"{depot_id}_{data['manifest_id']}.manifest"
            if manifest_path.exists():
                try:
                    manifest_content = manifest_path.read_bytes()
                    depot_data = {
                        'depot_id': depot_id,
                        'manifest_id': int(data['manifest_id']),
                        'depot_key': bytes.fromhex(data['key']),
                        'manifest_content': manifest_content
                    }
                    collected_depots.append(depot_data)
                    print(f"  -> Processed Depot {depot_id}")
                except Exception as e: print(f"  -> Error reading manifest for Depot {depot_id}: {e}")
            else: print(f"  -> Warning: Manifest file not found: {manifest_path.name}")
        
        if not collected_depots: print("Conversion failed."); return
        sfd_filename = Path(f"{self._sanitize_filename(game_name)}_converted.sfd")
        self._write_sfd_file(sfd_filename, app_id, collected_depots)
        
        print("\nAutomatically loading new .sfd file into queue...")
        self.sfd_path = sfd_filename.resolve()
        self._load_sfd_from_path(self.sfd_path)

    def make_sfd(self) -> None:
        """A utility to create an SFD file from scratch by fetching data from Steam."""
        if not self.client.logged_on: print("You must be logged in."); return
        try:
            app_id = int(input("Enter AppID: "))
        except ValueError: print("Invalid AppID."); return
        
        game_name = self._get_game_name(app_id)
        print(f"Creating SFD for '{game_name}'...")
        collected_depots: List[Dict[str, Any]] = []
        depot_num = 1
        while True:
            print("-" * 20)
            depot_id_str = input(f"DepotID #{depot_num} (or leave blank to finish): ")
            if not depot_id_str: break
            try:
                depot_id = int(depot_id_str)
                manifest_id = int(input(f"ManifestID for Depot {depot_id}: "))
                print("Fetching from Steam...")
                depot_key = self.cdn.get_depot_key(app_id, depot_id)
                code = self.cdn.get_manifest_request_code(app_id, depot_id, manifest_id)
                resp = self.cdn.cdn_cmd('depot', f'{depot_id}/manifest/{manifest_id}/5/{code}')
                if not (resp and resp.ok): raise ValueError("Failed to fetch manifest.")
                while True:
                    choice = input(f"Fetched Depot {depot_id}. Add it? (Y/N/Retry): ").upper()
                    if choice == 'Y':
                        collected_depots.append({'depot_id': depot_id, 'manifest_id': manifest_id, 'depot_key': depot_key, 'manifest_content': resp.content})
                        depot_num += 1; break
                    elif choice == 'N': depot_num += 1; break
                    elif choice == 'R': break
            except Exception as e:
                print(f"An error occurred: {e}")
                if input("Retry this depot? (Y/N): ").upper() != 'Y': depot_num += 1
        
        if not collected_depots: print("No depots collected."); return
        sfd_filename = Path(f"{self._sanitize_filename(game_name)}.sfd")
        self._write_sfd_file(sfd_filename, app_id, collected_depots)

    def _run_manifest_generator(self, app_id: int, output_dir: Path) -> None:
        """A helper to run the manifest generator, sharing the current client session."""
        print("\n--- Running Manifest Generator ---")
        if not self._ensure_logged_in():
            print("Cannot generate manifest without being logged in.")
            return
        try:
            generator = SteamManifestGenerator(app_id=app_id, output_dir=str(output_dir), client=self.client)
            generator.run()
        except Exception as e:
            print(f"An unexpected error occurred during manifest generation: {e}")

    def generate_manifest_workflow(self) -> None:
        """User-facing workflow to manually generate an appmanifest.acf file."""
        print("--- Manual App Manifest Generator ---")
        try:
            app_id = int(input("Enter the AppID to generate a manifest for: "))
        except ValueError:
            print("Invalid AppID.")
            return
        
        output_dir_str = input("Enter output directory (leave blank for current): ")
        output_dir = Path(output_dir_str) if output_dir_str else Path(".")
        
        self._run_manifest_generator(app_id, output_dir)
        
    def _verify_and_repair_file(self, file_info: Any, safe_path: Path) -> bool:
        """Verifies a local file against its manifest chunks. Truncates corruption."""
        if not safe_path.exists():
            file_info.seek(0); return False
        try:
            with safe_path.open('rb') as f:
                verified_offset = 0
                for chunk in file_info.chunks:
                    data = f.read(chunk.cb_original)
                    if not data: break
                    if hashlib.sha1(data).digest() != chunk.sha: break
                    verified_offset += chunk.cb_original
            if verified_offset == file_info.size:
                return True
            else:
                # Truncate the file to the last known-good byte to enable a safe resume.
                with safe_path.open('r+b') as f: f.truncate(verified_offset)
                file_info.seek(verified_offset); return False
        except IOError:
            file_info.seek(0); return False

    def _execute_verification_and_download_cycle(self, all_files: List[Any], base_dir: Path, verify_only: bool) -> bool:
        """
        Runs the core verification and download loop.
        Returns True on success, False if the user cancels.
        """
        # This is the main repair/download loop. It will continue until verification passes.
        while True:
            print("\nPHASE 1: Verifying local file integrity...")
            files_to_download = []
            total_download_size = 0
            
            for file_info in tqdm(all_files, desc="Verifying files"):
                if file_info.is_directory: continue
                safe_path = base_dir / file_info.filename
                
                if not self._verify_and_repair_file(file_info, safe_path):
                    files_to_download.append(file_info)
                    total_download_size += (file_info.size - file_info.offset)

            if not files_to_download:
                return True # Success! All files are present and correct.
            
            if verify_only:
                print(f"\nVerification failed. {len(files_to_download)} files need repair ({total_download_size/1024/1024:.2f} MB).")
                if input("Repair now? (Y/N): ").upper() != 'Y':
                    return False # User cancelled the repair.
                verify_only = False # Allow the download to proceed on this run.
            
            print(f"\nPHASE 2: Downloading {len(files_to_download)} files ({total_download_size/1024/1024:.2f} MB)...")
            input("Press Enter to start...")

            with tqdm(total=total_download_size, unit='B', unit_scale=True, desc="Downloading") as pbar:
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = [executor.submit(self._download_single_file, f, base_dir, pbar) for f in files_to_download]
                    concurrent.futures.wait(futures)

            print("\nPHASE 3: Running final verification...")

    def download_game(self, verification_only: bool = False) -> None:
        """Prepares for and initiates the download/verification process."""
        if not self.depots_to_download or not self.app_id:
            print("Download queue is empty."); return
        if not self._ensure_logged_in(): return
        
        game_name = self._get_game_name(self.app_id)
        
        self.overwrite_log = []
        master_file_map: Dict[str, Any] = {}
        print(f"\nAggregating files for '{game_name}'...")
        for depot in self.depots_to_download:
            try:
                manifest = self.cdn.get_manifest(self.app_id, depot['depot_id'], depot['manifest_id'])
                manifest.decrypt_filenames(depot['depot_key'])
                for file_info in manifest.iter_files():
                    if file_info.filename in master_file_map:
                        old_depot_id = master_file_map[file_info.filename][1]
                        self.overwrite_log.append(f"File '{file_info.filename}' from Depot {old_depot_id} was overwritten by Depot {depot['depot_id']}.")
                    master_file_map[file_info.filename] = (file_info, depot['depot_id'])
            except Exception as e:
                print(f"Warning: Could not process depot {depot['depot_id']}. Error: {e}")

        all_files_in_manifest = [item[0] for item in master_file_map.values()]
        if not all_files_in_manifest:
            print("No files to download."); return

        base_download_dir = Path(self._sanitize_filename(game_name)).resolve()
        base_download_dir.mkdir(parents=True, exist_ok=True)
        
        success = self._execute_verification_and_download_cycle(all_files_in_manifest, base_download_dir, verification_only)
        
        if success:
            print('\nGame Downloaded and Verified!')
            if self.overwrite_log:
                final_log_path = base_download_dir / 'overwritten_files.txt'
                temp_log_path = final_log_path.with_suffix('.tmp')
                temp_log_path.write_text("# File versions from depots listed LATER in the .sfd file were kept.\n\n" + "\n".join(self.overwrite_log))
                final_log_path.unlink(missing_ok=True)
                temp_log_path.rename(final_log_path)
                print(f"Overwrite log saved to {final_log_path}")

            # Automatically generate the manifest file on successful download.
            print("\nAutomatically generating appmanifest.acf...")
            self._run_manifest_generator(self.app_id, Path("."))


    def _download_single_file(self, file_info: Any, base_download_dir: Path, pbar: TqdmType) -> None:
        """The worker function for the download thread pool."""
        try:
            safe_path = base_download_dir / file_info.filename
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            with safe_path.open('ab') as f_out:
                for chunk_data in file_info:
                    f_out.write(chunk_data)
                    pbar.update(len(chunk_data))
        except Exception as e:
            # The next verification pass will catch and repair any resulting corrupt file.
            pbar.write(f"ERROR downloading {file_info.filename}: {e}")

    def run(self) -> None:
        """The main application loop and user interface."""
        print("!!! WARNING !!!\nTHIS SCRIPT INTERACTS WITH STEAM. USE AT YOUR OWN RISK.\n!!! ONLY LOAD .sfd's FROM TRUSTED SOURCES !!!")
        time.sleep(3)

        actions_without_pause = [8, 10, 11] # Login handles its own pause

        while True:
            self._clear_screen()
            depot_ids = [d['depot_id'] for d in self.depots_to_download]
            game_name_in_queue = self.app_name_cache.get(self.app_id, str(self.app_id)) if self.app_id else "N/A"
            
            print(f"""
Super Sexy Steam Downloader
      by PSS

Logged in:         {self.client.logged_on} ({self.client.username or 'Not logged in'})
Game in Queue:     {game_name_in_queue}
Depot(s) in Queue: {depot_ids or 'None'}

--- Main Workflow ---
1. Load SFD File into Queue
2. Download Game (from queue)
3. Verify/Repair an Existing Game Download

--- Converters & Utilities ---
4. Generate appmanifest.acf file
5. Convert LUA/Manifest files to SFD
6. Make SFD file from scratch (requires login)
7. AppID Lookup Tool
8. Login (Anonymous or with account)
9. Clear Download Queue
10. Logout
11. Exit
            """)
            
            try: selection = int(input('Selection (number): '))
            except ValueError: print("Invalid input."); time.sleep(1); continue
            
            action_map = {
                1: self.load_sfd_workflow,
                2: lambda: self.download_game(verification_only=False),
                3: lambda: self.download_game(verification_only=True),
                4: self.generate_manifest_workflow,
                5: self.convert_lua_workflow,
                6: self.make_sfd,
                7: self.app_id_lookup_tool,
                8: self.login,
                9: self._reset_queue,
            }

            if selection in action_map:
                action_map[selection]()
            elif selection == 10: self.client.logout(); print("Logged out."); time.sleep(1)
            elif selection == 11: print("Exiting."); sys.exit(0)
            else: print("Invalid selection."); time.sleep(1)

            if selection not in actions_without_pause and selection in action_map:
                input('Press Enter to continue...')


if __name__ == "__main__":
    app = SteamDownloaderApp()
    app.run()