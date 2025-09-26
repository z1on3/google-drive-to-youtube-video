#!/usr/bin/python
### edited 16.12.2024 for python 3 compat [c] Sami Karkar (patched)

import http.client as httplib
import httplib2
import os
import random
import sys
import time
import glob
from pathlib import Path

from apiclient.discovery import build
from apiclient.errors import HttpError
from apiclient.http import MediaFileUpload
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client.tools import argparser, run_flow

# Explicitly tell the underlying HTTP transport library not to retry, since
# we are handling retry logic ourselves.
httplib2.RETRIES = 1

# Maximum number of times to retry before giving up.
MAX_RETRIES = 10

# Always retry when these exceptions are raised.
RETRIABLE_EXCEPTIONS = (
    httplib2.HttpLib2Error, IOError, httplib.NotConnected,
    httplib.IncompleteRead, httplib.ImproperConnectionState,
    httplib.CannotSendRequest, httplib.CannotSendHeader,
    httplib.ResponseNotReady, httplib.BadStatusLine
)

# Always retry when an apiclient.errors.HttpError with one of these status
# codes is raised.
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

CLIENT_SECRETS_FILE = "client_secrets.json"

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

MISSING_CLIENT_SECRETS_MESSAGE = f"""
WARNING: Please configure OAuth 2.0

To make this sample run you will need to populate the client_secrets.json file
found at:

   {os.path.abspath(os.path.join(os.path.dirname(__file__), CLIENT_SECRETS_FILE))}

with information from the API Console
https://console.developers.google.com/

For more information about the client_secrets.json file format, please visit:
https://developers.google.com/api-client-library/python/guide/aaa_client_secrets
"""

VALID_PRIVACY_STATUSES = ("unlisted", "private", "public")


def get_authenticated_service(args):
    flow = flow_from_clientsecrets(
        CLIENT_SECRETS_FILE,
        scope=YOUTUBE_UPLOAD_SCOPE,
        message=MISSING_CLIENT_SECRETS_MESSAGE
    )

    storage = Storage(f"{sys.argv[0]}-oauth2.json")
    credentials = storage.get()

    if credentials is None or credentials.invalid:
        credentials = run_flow(flow, storage, args)

    return build(
        YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION,
        http=credentials.authorize(httplib2.Http())
    )


def initialize_upload(youtube, file_path, title, description, keywords, category, privacy_status):
    tags = keywords.split(",") if keywords else None

    body = dict(
        snippet=dict(
            title=title,
            description=description,
            tags=tags,
            categoryId=category
        ),
        status=dict(
            privacyStatus=privacy_status
        )
    )

    insert_request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=MediaFileUpload(file_path, chunksize=-1, resumable=True)
    )

    return resumable_upload(insert_request, file_path)


def resumable_upload(insert_request, file_path):
    response = None
    error = None
    retry = 0
    filename = os.path.basename(file_path)
    while response is None:
        try:
            print(f"Uploading {filename}...")
            status, response = insert_request.next_chunk()
            if response is not None:
                if 'id' in response:
                    print(f"Video '{filename}' (id: {response['id']}) was successfully uploaded.")
                    return response['id']
                else:
                    print(f"The upload of '{filename}' failed with an unexpected response: {response}")
                    return None
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                error = f"A retriable HTTP error {e.resp.status} occurred for '{filename}':\n{e.content}"
            else:
                raise
        except RETRIABLE_EXCEPTIONS as e:
            error = f"A retriable error occurred for '{filename}': {e}"

        if error is not None:
            print(error)
            retry += 1
            if retry > MAX_RETRIES:
                print(f"No longer attempting to retry '{filename}'.")
                return None

            max_sleep = 2 ** retry
            sleep_seconds = random.random() * max_sleep
            print(f"Sleeping {sleep_seconds:.2f} seconds and then retrying...")
            time.sleep(sleep_seconds)
            error = None
    return None


def select_folder():
    """Interactive folder selection"""
    current_path = Path.cwd()

    while True:
        print(f"\nCurrent directory: {current_path}")

        # List directories
        dirs = [d for d in current_path.iterdir() if d.is_dir()]
        dirs.sort()

        print("\nDirectories:")
        for i, d in enumerate(dirs, 1):
            print(f"  {i}. {d.name}")

        print("\nOptions:")
        print("  c. Choose current directory")
        print("  u. Go up one level")
        print("  q. Quit")

        choice = input("\nEnter your choice: ").strip().lower()

        if choice == 'c':
            return current_path
        elif choice == 'u':
            current_path = current_path.parent
        elif choice == 'q':
            exit("Cancelled by user.")
        elif choice.isdigit():
            dir_idx = int(choice) - 1
            if 0 <= dir_idx < len(dirs):
                current_path = dirs[dir_idx]
            else:
                print("Invalid directory number.")
        else:
            print("Invalid choice.")


def select_video_files(folder_path):
    """Interactive video file selection with support for comma-separated numbers"""
    # Common video extensions
    video_extensions = ['*.mp4', '*.avi', '*.mov', '*.mkv', '*.wmv', '*.flv', '*.webm', '*.m4v']

    video_files = []
    for ext in video_extensions:
        video_files.extend(folder_path.glob(ext))
        video_files.extend(folder_path.glob(ext.upper()))

    video_files = list(set(video_files))  # Remove duplicates
    video_files.sort()

    if not video_files:
        print(f"No video files found in {folder_path}")
        return []

    print(f"\nVideo files in {folder_path}:")
    for i, f in enumerate(video_files, 1):
        file_size = f.stat().st_size / (1024*1024)  # Size in MB
        print(f"  {i}. {f.name} ({file_size:.1f} MB)")

    while True:
        choice = input(f"\nEnter file numbers (comma-separated, e.g., 1,3,4,5) or 'all' for all files: ").strip()

        if choice.lower() == 'all':
            return video_files

        if choice.lower() == 'q':
            return []

        try:
            # Parse comma-separated numbers
            selected_indices = [int(x.strip()) - 1 for x in choice.split(',')]

            # Validate indices
            selected_files = []
            for idx in selected_indices:
                if 0 <= idx < len(video_files):
                    selected_files.append(video_files[idx])
                else:
                    print(f"Invalid file number: {idx + 1}")
                    break
            else:
                # All indices were valid
                return selected_files

        except ValueError:
            print("Invalid input. Please enter numbers separated by commas (e.g., 1,3,4,5)")


def get_video_metadata(file_path, default_title, default_description, default_keywords, default_category, default_privacy):
    """Get metadata for a video file"""
    filename = file_path.stem

    print(f"\n--- Metadata for {file_path.name} ---")

    title = input(f"Title (default: {filename}): ").strip() or filename
    description = input(f"Description (default: {default_description}): ").strip() or default_description
    keywords = input(f"Keywords (default: {default_keywords}): ").strip() or default_keywords
    category = input(f"Category (default: {default_category}): ").strip() or default_category
    privacy = input(f"Privacy ({'/'.join(VALID_PRIVACY_STATUSES)}, default: {default_privacy}): ").strip() or default_privacy

    if privacy not in VALID_PRIVACY_STATUSES:
        print(f"Invalid privacy status. Using {default_privacy}")
        privacy = default_privacy

    return title, description, keywords, category, privacy


if __name__ == '__main__':
    # Add argument for interactive mode
    argparser.add_argument("--interactive", "-i", action="store_true", help="Use interactive mode for folder and file selection")
    argparser.add_argument("--file", help="Video file to upload (non-interactive mode)")
    argparser.add_argument("--files", nargs="*", help="Multiple video files to upload (non-interactive mode)")
    argparser.add_argument("--title", help="Video title", default="Test Title")
    argparser.add_argument("--description", help="Video description", default="Test Description")
    argparser.add_argument("--category", default="22",
        help="Numeric video category. See https://developers.google.com/youtube/v3/docs/videoCategories/list")
    argparser.add_argument("--keywords", help="Video keywords, comma separated", default="")
    argparser.add_argument("--privacyStatus", choices=VALID_PRIVACY_STATUSES,
        default=VALID_PRIVACY_STATUSES[0], help="Video privacy status.")
    argparser.add_argument("--same-metadata", action="store_true",
        help="Use same metadata for all videos (interactive mode)")

    args = argparser.parse_args()

    video_files = []

    if args.interactive:
        # Interactive mode
        print("=== Interactive Video Upload ===")

        # Select folder
        folder = select_folder()
        print(f"\nSelected folder: {folder}")

        # Select video files
        video_files = select_video_files(folder)

        if not video_files:
            exit("No video files selected.")

        print(f"\nSelected {len(video_files)} video file(s):")
        for f in video_files:
            print(f"  - {f.name}")

    else:
        # Non-interactive mode
        if args.files:
            # Multiple files specified
            for file_path in args.files:
                if os.path.exists(file_path):
                    video_files.append(Path(file_path))
                else:
                    print(f"Warning: File not found: {file_path}")
        elif args.file:
            # Single file specified
            if os.path.exists(args.file):
                video_files.append(Path(args.file))
            else:
                exit(f"File not found: {args.file}")
        else:
            exit("Please specify video file(s) using --file, --files, or use --interactive mode.")

    if not video_files:
        exit("No valid video files to upload.")

    # Get YouTube service
    youtube = get_authenticated_service(args)

    # Upload videos
    successful_uploads = []
    failed_uploads = []

    print(f"\n=== Starting upload of {len(video_files)} video(s) ===")

    for i, video_file in enumerate(video_files, 1):
        print(f"\n[{i}/{len(video_files)}] Processing: {video_file.name}")

        try:
            if args.interactive and not args.same_metadata:
                # Get individual metadata for each video
                title, description, keywords, category, privacy = get_video_metadata(
                    video_file, args.title, args.description, args.keywords,
                    args.category, args.privacyStatus
                )
            else:
                # Use default/same metadata for all videos
                title = args.title if len(video_files) == 1 else f"{args.title} - {video_file.stem}"
                description = args.description
                keywords = args.keywords
                category = args.category
                privacy = args.privacyStatus

            # Upload video
            video_id = initialize_upload(youtube, str(video_file), title, description,
                                       keywords, category, privacy)

            if video_id:
                successful_uploads.append((video_file.name, video_id))
            else:
                failed_uploads.append(video_file.name)

        except HttpError as e:
            print(f"An HTTP error {e.resp.status} occurred for '{video_file.name}':\n{e.content}")
            failed_uploads.append(video_file.name)
        except Exception as e:
            print(f"An error occurred for '{video_file.name}': {e}")
            failed_uploads.append(video_file.name)

    # Final summary
    print(f"\n=== Upload Summary ===")
    print(f"Successful uploads: {len(successful_uploads)}")
    for filename, video_id in successful_uploads:
        print(f"  ✓ {filename} -> https://youtube.com/watch?v={video_id}")

    if failed_uploads:
        print(f"\nFailed uploads: {len(failed_uploads)}")
        for filename in failed_uploads:
            print(f"  ✗ {filename}")

    print(f"\nTotal: {len(successful_uploads)} successful, {len(failed_uploads)} failed")
