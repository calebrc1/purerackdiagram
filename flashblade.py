
import asyncio
from utils import RackImage, combine_images_vertically


class FBDiagram():
    def __init__(self, params):
        self.config = {}
        self.config["chassis"] = int(params.get("chassis", 1))
        self.config["face"] = params.get("face", "front").lower()
        self.config['direction'] = params.get("direction","up").lower()

    async def get_image(self):
        tasks = []

        img_key = "png/pure_fb_{}.png".format(self.config["face"])

        for _ in range(self.config["chassis"]):
            tasks.append(RackImage(img_key).get_image())

        all_images = await asyncio.gather(*tasks)
        if self.config["direction"] == "up":
            all_images.reverse()

        return combine_images_vertically(all_images)