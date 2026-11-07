"""Actual decoder-child ownership survives spawn/reap cancellation gaps."""
import asyncio
import sys
from pathlib import Path
from uuid import uuid4
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))


@pytest.mark.asyncio
async def test_image_spawn_handle_gap_retains_physical_ref_until_actual_child_reaped(tmp_path,monkeypatch):
    from b2_owned_media import owned_media
    from platform_service.media.artifacts import ArtifactStore
    from platform_service.media_maintenance.runtime import physical_call
    from platform_service.media_maintenance.contracts import MaintenanceError
    async with owned_media(tmp_path) as value:
        generation=value['generation'];permit=await generation.acquire('upload',str(uuid4()))
        actual_spawn=asyncio.create_subprocess_exec;entered=asyncio.Event();release=asyncio.Event();children=[]
        async def delayed_handle(*args,**kwargs):
            process=await actual_spawn(sys.executable,'-I','-c','import sys;sys.stdin.buffer.read()',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            children.append(process);entered.set();await release.wait();return process
        monkeypatch.setattr(asyncio,'create_subprocess_exec',delayed_handle)
        task=asyncio.create_task(physical_call(ArtifactStore(tmp_path/'media').put_image_async,b'synthetic',permit=permit))
        try:
            await asyncio.wait_for(entered.wait(),2);assert children[0].returncode is None
            task.cancel();await asyncio.sleep(.03)
            assert not task.done(),'parent released before OS child handle/reap ownership returned'
            assert permit.physical_count==1
            with pytest.raises(MaintenanceError):value['locks'].acquire_exclusive()
            task.cancel();release.set()
            with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,2)
            assert children[0].returncode is not None and permit.physical_count==0
            await generation.finish(permit,{'no_write':True})
        finally:
            release.set()
            if not task.done():
                task.cancel()
                try:await task
                except BaseException:pass
            for process in children:
                if process.returncode is None:process.kill()
                await process.communicate()


@pytest.mark.asyncio
async def test_image_reap_survives_repeated_parent_cancellation(tmp_path,monkeypatch):
    from b2_owned_media import owned_media
    from platform_service.media.artifacts import ArtifactStore
    from platform_service.media_maintenance.runtime import physical_call
    async with owned_media(tmp_path) as value:
        generation=value['generation'];permit=await generation.acquire('upload',str(uuid4()))
        actual_spawn=asyncio.create_subprocess_exec;spawned=asyncio.Event();reaping=asyncio.Event();release=asyncio.Event();children=[]
        async def owned_child(*args,**kwargs):
            process=await actual_spawn(sys.executable,'-I','-c','import time;time.sleep(30)',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            children.append(process);original_wait=process.wait
            async def delayed_wait():
                reaping.set();await release.wait();return await original_wait()
            process.wait=delayed_wait;spawned.set();return process
        monkeypatch.setattr(asyncio,'create_subprocess_exec',owned_child)
        task=asyncio.create_task(physical_call(ArtifactStore(tmp_path/'media').put_image_async,b'synthetic',permit=permit))
        try:
            await asyncio.wait_for(spawned.wait(),2);await asyncio.sleep(.01);task.cancel()
            await asyncio.wait_for(reaping.wait(),2);task.cancel();await asyncio.sleep(.03)
            assert not task.done(),'repeated cancellation dropped actual child-reap ownership'
            assert permit.physical_count==1
            release.set()
            with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,2)
            assert children[0].returncode is not None and permit.physical_count==0
            await generation.finish(permit,{'no_write':True})
        finally:
            release.set()
            if not task.done():
                task.cancel()
                try:await task
                except BaseException:pass
            for process in children:
                if process.returncode is None:process.kill()
                await process.communicate()
