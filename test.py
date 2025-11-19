import asyncio
import aiohttp
import json

async def test_server():
    """Test if the server is running correctly"""
    try:
        async with aiohttp.ClientSession() as session:
            # Test HTTP server
            async with session.get('http://localhost:8080/health') as resp:
                health = await resp.json()
                print(f"Health check: {health}")
                
            # Test WebSocket connection
            async with session.ws_connect('http://localhost:8080/ws') as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        print(f"WebSocket message: {data}")
                        break
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break
                        
    except Exception as e:
        print(f"Test failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_server())