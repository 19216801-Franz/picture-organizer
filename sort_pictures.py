#!/bin/env python3
import argparse
import sys
import os
import datetime
import shutil
import json
import ffmpeg
import filecmp
from rich.progress import track, Progress
from PIL import Image
from PIL import ExifTags

allowed_picture_extensions = ['.jpg', '.JPG', '.jpeg', '.JPEG', '.png', '.PNG', '.gif', '.GIF', '.NEF', '.CR2', '.dng', '.bmp']
pictures_with_exif = ['.jpg', '.JPG', '.jpeg', '.JPEG']

def sort_file_in(outfile: os.PathLike, infile: os.PathLike) -> None:
    dirname = os.path.dirname(outfile)

    if not os.path.exists(dirname):
        os.makedirs(dirname)

    if os.path.exists(outfile):
        raise ValueError(f'Won\'t copy {infile} to {outfile}. File already exists!')
    
    shutil.move(infile, outfile)

def create_moves(pictures:list, out:os.PathLike) -> dir:
    mapping = {}
    for timestamp, path in track(pictures, "[green]Sorting files by date...", total=len(pictures)):
        directory = os.path.join(out, str(timestamp.year))
        file_ext = os.path.basename(path).split('.')[-1]
        filename = timestamp.strftime('%m_%d_%H_%M_%S__%-dth_of_%B_at_%Hh_%Mm') + '.' + file_ext
        new_name = os.path.join(directory, filename)

        if new_name in mapping:
            mapping[new_name].append(path)
        else:
            mapping[new_name] = [path]

    moves = {}
    for new_name, files in track(mapping.items(), "[green]Finishing up sorting...", total=len(mapping)):
        if len(files) == 1:
            moves[new_name] = files[0]
        else:
            for i, file in enumerate(files):
                dir_and_filename, file_ext = new_name.split('.')
                name = f'{dir_and_filename}_{i+1}.{file_ext}'
                moves[name] = file

    return moves

def check_update_old_info(moves:dict, json_file: os.PathLike, dry_run=True) -> (dict, int):
    deleted_duplicates = 0
    old_info = {}
    if os.path.exists(json_file):
        with open(json_file, 'r') as f:
            old_info = json.load(f)

    for k, v in track(old_info.items(), description="[green]Checking for duplicates in old info.json ...", total=len(old_info)):
        old_filename = os.path.basename(k)
        if old_filename in [os.path.basename(new_file) for new_file in moves.keys()]:
            existing_file = k
            incoming_files = [new_file for new_file in moves.keys() if old_filename in new_file]
            if len(incoming_files) != 1:
                raise ValueError(f'Found more than one file with the same name like old ({k}) in the new files')
            incoming_file = moves[incoming_files[0]]
            del moves[incoming_files[0]]

            # TODO: hack the correct location if I rename the folder
            if not filecmp.cmp(existing_file.replace('/pictures_sorted/', '/pictures/'), incoming_file, shallow=False):
                print(f'Files {existing_file} and {incoming_file} are different. Please compare manually!')
            else:
                if not dry_run:
                    deleted_duplicates += 1
                    os.remove(incoming_file)
    return moves, deleted_duplicates

def append_moves_to_json(moves: dict, json_file: os.PathLike, dry_run=True) -> dict:
    old_info = {}
    if os.path.exists(json_file):
        with open(json_file, 'r') as f:
            old_info = json.load(f)

    for k, v in track(old_info.items(), description="[green]Updating info.json ...", total=len(old_info)):
        if k in moves:
            raise print(f'ERROR: {k} still exists in the new info.json, this shouldn\'t have happened')

    old_info.update(moves)
        
    if not dry_run:
        if not os.path.exists(json_file):
            os.makedirs(os.path.split(json_file)[0], exist_ok=True)

        with open(json_file, 'w') as f:
            json.dump(old_info, f)
    
    return old_info

def do_move_files(pictures: list, out: os.PathLike) -> None:
    print('Moving files...')

    moves = create_moves(pictures, out)

    moves, deleted_duplicates = check_update_old_info(moves, os.path.join(out, 'info.json'), dry_run=False)

    for new_file, old_file in track(moves.items(), description="[red]Moving files...", total=len(moves)):
        sort_file_in(new_file, old_file)
    
    append_moves_to_json(moves, os.path.join(out, 'info.json'), dry_run=False)
    if deleted_duplicates > 0:
        print(f"Deleted {deleted_duplicates} duplicates")

def dryrun_move_files(pictures: list, out: os.PathLike) -> None:
    moves = create_moves(pictures, out)

    moves, deleted_duplicates = check_update_old_info(moves, os.path.join(out, 'info.json'))

    for new_name, file in moves.items():
        print(f"{file} --> \t\t{new_name}")

    append_moves_to_json(moves, os.path.join(out, 'info.json'))
    if deleted_duplicates > 0:
        print(f"Would have deleted {deleted_duplicates} duplicates")

def extract_timestamp_from_filemeta(path: os.PathLike) -> datetime.datetime:
    ctime = os.path.getctime(path)
    mtime = os.path.getmtime(path)
    min_time = datetime.datetime.fromtimestamp(min(ctime, mtime))
    if min_time.year == datetime.datetime.now().year and min_time.month == datetime.datetime.now().month == min_time.day == datetime.datetime.now().day:
        raise ValueError(f'Suspicious timestamp {min_time}')
    return min_time
    
def extract_picture_timestamp(path: os.PathLike) -> datetime.datetime:
    if any(path.endswith(ext) for ext in pictures_with_exif):
        img = Image.open(path)
        exif_data = img._getexif()
        
        if exif_data:
            tags = {ExifTags.TAGS[k]: v for k, v in Image.open(path)._getexif().items() if k in ExifTags.TAGS}
            date_tags = ['DateTime', 'DateTimeOriginal', 'DateTimeDigitized']
            dates = [tags.get(tag) for tag in date_tags if tag in tags]

            if len(dates) > 0:
                return datetime.datetime.strptime(min(dates), '%Y:%m:%d %H:%M:%S')
    
    # If exif data is not available, use the file's first modified time
    return extract_timestamp_from_filemeta(path)

def find_pictures(source: str) -> list:
    if not os.path.isdir(source):
        raise ValueError('Source is not a directory')

    pictures = []
    unmatched = []
    errors = []

    total_files = len([f for _, _, files in os.walk(source) for f in files])

    with Progress() as progress:
        task = progress.add_task("[green]Processing files...", total=total_files)

        for root, dirs, files in os.walk(source):
            for file in files:
                path = os.path.join(root, file)
                if any(file.endswith(ext) for ext in allowed_picture_extensions):


                    try:
                        timestamp = extract_picture_timestamp(path)
                        pictures.append((timestamp, path))
                    except Exception as e:
                        errors.append((e, path))

                elif file.endswith('.mp4') or file.endswith('.MP4') or file.endswith('.mov') or file.endswith('.MOV') or file.endswith('.avi') or file.endswith('.AVI'):
                    try:
                        probe = ffmpeg.probe(path)
                        if 'streams' in probe and len(probe['streams']) > 0 and 'tags' in probe['streams'][0] and 'creation_time' in probe['streams'][0]['tags']:
                            timestamp = datetime.datetime.fromisoformat(probe['streams'][0]['tags']['creation_time'])
                        elif 'format' in probe and 'tags' in probe['format'] and 'creation_time' in probe['format']['tags']:
                            timestamp = datetime.datetime.fromisoformat(probe['format']['tags']['creation_time'])
                        else:
                            timestamp = extract_timestamp_from_filemeta(path)
                        pictures.append((timestamp, path))
                    except Exception as e:
                        errors.append((e, path))
                else:
                    unmatched.append(({}, path))

                progress.update(task, advance=1)

    print(f'Found {len(pictures)} valid files and {len(unmatched) + len(errors)} invalid files...')
    return pictures, unmatched, errors

def cleanup(root: os.PathLike) -> int:

    deleted = set()
    
    for current_dir, subdirs, files in os.walk(root, topdown=False):

        still_has_subdirs = False
        for subdir in subdirs:
            if os.path.join(current_dir, subdir) not in deleted:
                still_has_subdirs = True
                break
    
        if not any(files) and not still_has_subdirs:
            os.rmdir(current_dir)
            deleted.add(current_dir)

    return len(deleted)

def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description='Sort pictures by date')
    parser.add_argument('source', help='Source directory')
    parser.add_argument('--out', help='Output directory', required=False)
    parser.add_argument('--apply', help='Move and delete files (dryrun without)', action='store_true')
    parser.add_argument('--verbose', help='Print more information', action='store_true')
    parser.add_argument('--cleanup', help='Remove empty directories', action='store_true')

    args = parser.parse_args(argv[1:])

    args.source = os.path.abspath(args.source)

    print(f"Source: {args.source}")

    if args.out is None:
        base, old_dir = os.path.split(args.source)
        args.out = os.path.join(base, 'pictures')
        print(f"Output dir wasn't specified, so {args.out} will be used")

    pictures, unmatched, errors = find_pictures(args.source)

    if args.apply:
        do_move_files(pictures, args.out)
    else:
        dryrun_move_files(pictures, args.out)

    if args.verbose and len(unmatched) + len(errors) > 0:
        print('Files that weren\'t pictures:')
        for _, path in unmatched:
            print(path)

    if args.verbose and len(errors) > 0:
        print('Files that had errors:')
        for e, path in errors:
            print(f'{path}: {e}')

    if args.cleanup:
        print(f"Deleted {cleanup(args.source)} empty directories")

if __name__ == '__main__':
    sys.exit(main(sys.argv))

