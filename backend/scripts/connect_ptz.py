import asyncio
import os

from app.cameras.service import CameraService

async def main():
    service = CameraService()
    # Replace camera_id if you manage multiple cameras
    camera_id = 1
    host = os.environ.get('PTZ_CAMERA_HOST', '192.168.1.200')
    username = os.environ.get('PTZ_CAMERA_USER', 'admin')
    password = os.environ.get('PTZ_CAMERA_PASS', 'smgadmin')
    port = int(os.environ.get('PTZ_CAMERA_PORT', '80'))
    visca_port = int(os.environ.get('PTZ_VISCA_PORT', '1240'))
    visca_udp = os.environ.get('PTZ_VISCA_UDP', '').lower() in ('1', 'true', 'yes')

    service.register_camera(camera_id, host, port, username, password, visca_port=visca_port, visca_udp=visca_udp)
    ok = await service.connect_camera(camera_id)
    print(f'Connected: {ok}')
    if ok:
        state = await service.get_camera_state(camera_id)
        print('State:', state)
        preset = os.environ.get('PTZ_TEST_PRESET')
        if preset is not None:
            moved = await service.move_to_preset(camera_id, int(preset))
            print(f'Recall preset {preset}: {moved}')

if __name__ == '__main__':
    asyncio.run(main())
