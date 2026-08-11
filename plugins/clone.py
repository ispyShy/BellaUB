from telethon import events
from telethon.tl import functions, types
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.photos import DeletePhotosRequest, UploadProfilePhotoRequest
from telethon.tl.types import InputPhoto
from utils.utils import CipherElite
from utils.decorators import rishabh
from plugins.bot import add_handler
import html
import os
import json
import logging
import asyncio

VERSION = "1.1.0"
CATEGORY = "utilities"

logger = logging.getLogger(__name__)
BACKUP_DIR = "DB/profile_backups"


def init(client_instance):
    commands = [
        ".yoink <username/userid/reply> - Clone a user's profile (name, bio, pfp/GIF pfp)",
        ".revert - Revert to your original profile"
    ]
    description = "Clone (yoink) and revert user profiles, including animated pfps"
    add_handler("yoink", commands, description)


os.makedirs(BACKUP_DIR, exist_ok=True)


async def delete_messages_after_delay(event, response, delay=5):
    """Delete both command and response messages after delay"""
    await asyncio.sleep(delay)
    try:
        await event.delete()
    except Exception as e:
        logger.warning(f"Failed to delete command: {e}")
    try:
        await response.delete()
    except Exception as e:
        logger.warning(f"Failed to delete response: {e}")


async def download_animated_profile_photo(client, entity, save_path):
    """
    Attempts to download the animated (video) profile photo of an entity.
    Returns the path to the saved mp4 file, or None if the user has no
    animated pfp / download failed.
    """
    try:
        photos = await client.get_profile_photos(entity, limit=1)
        if not photos:
            return None

        photo = photos[0]
        if not isinstance(photo, types.Photo) or not photo.video_sizes:
            return None

        video_size = next(
            (s for s in photo.video_sizes if isinstance(s, types.VideoSize)),
            None
        )
        if not video_size:
            return None

        location = types.InputPhotoFileLocation(
            id=photo.id,
            access_hash=photo.access_hash,
            file_reference=photo.file_reference,
            thumb_size=video_size.type
        )

        await client.download_file(location, file=save_path)
        return save_path if os.path.exists(save_path) else None

    except Exception as e:
        logger.warning(f"Animated profile photo download failed: {e}")
        return None


async def upload_profile_photo(client, image_path=None, video_path=None):
    """
    Uploads a profile photo/video. Prefers the animated video if provided.
    """
    if video_path and os.path.exists(video_path):
        with open(video_path, 'rb') as f:
            uploaded = await client.upload_file(f)
        await client(UploadProfilePhotoRequest(video=uploaded, video_start_ts=0.0))
        return

    if image_path and os.path.exists(image_path):
        with open(image_path, 'rb') as f:
            uploaded = await client.upload_file(f)
        await client(UploadProfilePhotoRequest(file=uploaded))
        return

    raise ValueError("No valid photo/video available to upload")


async def register_commands():

    @CipherElite.on(events.NewMessage(pattern=r"\.yoink"))
    @rishabh()
    async def yoink_profile(event):
        response = None
        temp_files = []
        try:
            sender_id = event.sender_id
            backup_file = os.path.join(BACKUP_DIR, f"{sender_id}.json")

            # ---- Create backup if it doesn't already exist ----
            if not os.path.exists(backup_file):
                me = await event.client.get_entity("me")
                my_full = await event.client(GetFullUserRequest(me))

                photo_path = await event.client.download_profile_photo(
                    "me", file=os.path.join(BACKUP_DIR, f"{sender_id}_photo")
                )

                video_backup_path = os.path.join(BACKUP_DIR, f"{sender_id}_video.mp4")
                video_path = await download_animated_profile_photo(
                    event.client, "me", video_backup_path
                )

                backup_data = {
                    "first_name": me.first_name or "",
                    "last_name": me.last_name or "",
                    "about": my_full.full_user.about if my_full.full_user else "",
                    "photo_path": photo_path,
                    "video_path": video_path
                }

                with open(backup_file, 'w') as f:
                    json.dump(backup_data, f)
                logger.info(f"Created profile backup for {sender_id}")

            # ---- Get target user ----
            target_user = await get_user_from_event(event)
            if not target_user:
                response = await event.reply("❌ No user specified to yoink!")
                asyncio.create_task(delete_messages_after_delay(event, response))
                return

            user_id = target_user.id
            full_user = await event.client(GetFullUserRequest(target_user))

            # ---- Try to grab animated pfp first, fallback to static ----
            video_temp_path = os.path.join(BACKUP_DIR, f"temp_yoink_{user_id}.mp4")
            video_path = await download_animated_profile_photo(
                event.client, user_id, video_temp_path
            )

            profile_pic = None
            if not video_path:
                profile_pic = await event.client.download_profile_photo(
                    user_id, file=f"temp_yoink_{user_id}"
                )

            if video_path:
                temp_files.append(video_path)
            if profile_pic:
                temp_files.append(profile_pic)

            # ---- Update name/bio ----
            first_name = html.escape(target_user.first_name or "")
            last_name = html.escape(target_user.last_name or "") or "⁪⁬⁮⁮ ‌‌‌‌"

            await event.client(functions.account.UpdateProfileRequest(
                first_name=first_name,
                last_name=last_name
            ))

            if full_user.full_user and full_user.full_user.about:
                await event.client(functions.account.UpdateProfileRequest(
                    about=full_user.full_user.about
                ))

            # ---- Handle profile photo/video ----
            photo_error = None
            if profile_pic or video_path:
                try:
                    await upload_profile_photo(
                        event.client, image_path=profile_pic, video_path=video_path
                    )
                except Exception as e:
                    photo_error = str(e)
                    logger.warning(f"Profile photo upload failed: {e}")
                finally:
                    for p in temp_files:
                        if p and os.path.exists(p):
                            os.remove(p)

            kind = "animated pfp 🎞️" if video_path else ("pfp 🖼️" if profile_pic else "no pfp")
            if photo_error:
                response = await event.reply(f"👥 Profile yoinked! ⚠️ Photo failed: {photo_error}")
            else:
                response = await event.reply(f"👥 Profile successfully yoinked! ({kind})")

        except Exception as e:
            logger.error(f"Yoink error: {e}", exc_info=True)
            response = await event.reply(f"❌ Yoink failed: {str(e)}")
        finally:
            for p in temp_files:
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            if response:
                asyncio.create_task(delete_messages_after_delay(event, response))

    @CipherElite.on(events.NewMessage(pattern=r"\.revert"))
    @rishabh()
    async def revert_profile(event):
        response = None
        try:
            sender_id = event.sender_id
            backup_file = os.path.join(BACKUP_DIR, f"{sender_id}.json")

            if not os.path.exists(backup_file):
                response = await event.reply("❌ No backup found! Please yoink first before reverting.")
                asyncio.create_task(delete_messages_after_delay(event, response))
                return

            with open(backup_file, 'r') as f:
                backup_data = json.load(f)

            # ---- Revert name/bio ----
            await event.client(functions.account.UpdateProfileRequest(
                first_name=backup_data.get("first_name", ""),
                last_name=backup_data.get("last_name", "")
            ))

            await event.client(functions.account.UpdateProfileRequest(
                about=backup_data.get("about", "")
            ))

            # ---- Revert profile photo/video ----
            photo_error = None
            try:
                current_photos = await event.client.get_profile_photos("me")
                if current_photos:
                    input_photos = [
                        InputPhoto(
                            id=photo.id,
                            access_hash=photo.access_hash,
                            file_reference=photo.file_reference
                        ) for photo in current_photos
                    ]
                    await event.client(DeletePhotosRequest(id=input_photos))

                video_path = backup_data.get("video_path")
                photo_path = backup_data.get("photo_path")

                if video_path and os.path.exists(video_path):
                    await upload_profile_photo(event.client, video_path=video_path)
                elif photo_path and os.path.exists(photo_path):
                    await upload_profile_photo(event.client, image_path=photo_path)

            except Exception as e:
                photo_error = str(e)
                logger.warning(f"Photo revert error: {e}")

            # ---- Cleanup backup files ----
            for key in ("photo_path", "video_path"):
                p = backup_data.get(key)
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

            if os.path.exists(backup_file):
                os.remove(backup_file)

            if photo_error:
                response = await event.reply(f"🔄 Profile reverted! ⚠️ Photo failed: {photo_error}")
            else:
                response = await event.reply("🔄 Profile successfully reverted!")

        except Exception as e:
            logger.error(f"Revert error: {e}", exc_info=True)
            response = await event.reply(f"❌ Revert failed: {str(e)}")
        finally:
            if response:
                asyncio.create_task(delete_messages_after_delay(event, response))


async def get_user_from_event(event):
    try:
        if event.reply_to_msg_id:
            reply_message = await event.get_reply_message()
            return await event.client.get_entity(reply_message.sender_id)
        elif event.pattern_match.group(1):
            user_input = event.pattern_match.group(1).strip()
            return await event.client.get_entity(user_input)
        return None
    except Exception as e:
        logger.error(f"User fetch error: {e}")
        return None
