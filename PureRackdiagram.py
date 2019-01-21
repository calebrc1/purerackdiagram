"""
Builds a rack diagram and caches results.
"""
import time
program_time_s = time.time()
import os
import sys
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
from io import BytesIO
import asyncio
from datetime import datetime
import concurrent
import base64
import boto3
import queue

import logging
import hashlib
from threading import Thread
from concurrent.futures import CancelledError

logger = logging.getLogger()
logger.setLevel(logging.INFO)


cache = {}
cache_lock = asyncio.Lock()
version = 1

session = boto3.session.Session()
s3 = session.client('s3')
s3_base_url = 'https://s3.amazonaws.com/'
bucket = 'images.purestorage'


def timeit(method):
    def timed(*args, **kw):
        ts = time.time()
        result = method(*args, **kw)
        te = time.time()
        logger.info('{:>5}  Duration: {:.2f} ms, Start: {:.2f} - {:.2f}'.format(
                    method.__name__,
                    (te - ts) * 1000,
                    (ts - program_time_s)*1000,
                    (te - program_time_s)*1000 ))
        return result
    return timed


class RackImage():
    def __init__(self, key):
        self.key = key
        self.img = None
        self.io_lock = asyncio.Lock()

        if key in cache:
            self.primary = False
            self.primary_obj = cache[key]
        else:
            cache[key] = self
            self.primary = True

        
    async def get_image(self):
        if not self.primary:
            return await self.primary_obj.get_image()
        
        async with self.io_lock:
            
            if self.img:
                return self.img.copy()
            
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                await loop.run_in_executor(pool, self.load_img)

            return self.img.copy()
    
    @timeit
    def load_img(self):
        self.img = Image.open(self.key)
        self.img.load()
        logger.info("Loaded: {}".format(self.key))



class FAShelf():
    def __init__(self, params):
        self.config = params
        self.start_img_event = asyncio.Event()

    async def get_image(self):
        c = self.config
        tasks = []
        tasks.append(self.get_base_img())
        if c["face"] == "front":
            if c["shelf_type"] == "nvme":
                tasks.append(self.add_nvme_fms())
            else:
                tasks.append(self.add_sas_fms())

        await asyncio.gather(*tasks)
        return self.tmp_img

    async def get_base_img(self):
        c = self.config
        key = "png/pure_fa_{}_shelf_{}.png".format(c["shelf_type"], c["face"])
        self.tmp_img = await RackImage(key).get_image()
        self.start_img_event.set()

    async def add_nvme_fms(self):
        cur_module = 0

        for dp in self.config["datapacks"]:
            fm_str = dp[0]
            fm_type = dp[1]
            num_modules = dp[2]

            if num_modules > 0:
                fm_img = await RackImage("png/pure_fa_dfm.png").get_image()
                if self.config['fm_label']:
                    apply_fm_label(fm_img, fm_str, fm_type)

                await self.start_img_event.wait()
                fm_loc = get_chassis_fm_loc()
                fm_rotated = fm_img.rotate(-90, expand=True)

                for x in range(cur_module, min(28, num_modules + cur_module)):
                    if x < 20:
                        self.tmp_img.paste(fm_img, fm_loc[x])
                    else:
                        self.tmp_img.paste(fm_rotated, fm_loc[x])
                cur_module += num_modules

        right = False
        if self.config['dp_label']:
            for dp in self.config["datapacks"]:
                dp_size = dp[3]
    
                y_offset = 50
                x_offset = 162
                self.tmp_img = apply_dp_label(self.tmp_img, dp_size, x_offset, y_offset, right)
                right = True


    async def add_sas_fms(self):
        cur_module = 0
        for dp in self.config["datapacks"]:
            fm_str = dp[0]
            fm_type = dp[1]
            num_modules = dp[2]
            dp_size = dp[3]

            dp_start = cur_module

            if num_modules > 0:
                fm_img = await RackImage("png/pure_fa_dfm.png").get_image()

                if self.config['fm_label']:
                    apply_fm_label(fm_img, fm_str, fm_type)


                await self.start_img_event.wait()
                fm_loc = get_sas_fm_loc()

                for x in range(cur_module, min(24, cur_module + num_modules)):
                    self.tmp_img.paste(fm_img, fm_loc[x])
            
                    # self.tmp_img.save('tmp.png')
                cur_module += num_modules

                if self.config['dp_label']:
                    y_offset = 0
                    x_offset = 90
                    right = False
                    if dp_start > 9:
                        right = True
                    self.tmp_img = apply_dp_label(self.tmp_img, dp_size, x_offset, y_offset, right)


class FAChassis():

    @timeit
    def __init__(self, params):
        config = params.copy()
        del config["shelves"]
        self.config = config
        self.start_img_event = asyncio.Event()
        self.ch0_fm_loc = None
        
    @timeit
    async def get_image(self):
        c = self.config
        key = "png/pure_fa_{}".format(c["generation"])

        if c["face"] == "front" and c["bezel"]:
            key += "_bezel.png"
            return await RackImage(key).get_image()

        # not doing bezel
        key += "_{}.png".format(c["face"])

        tasks = []
        tasks.append(self.get_base_img(key))

        if c["face"] == "front":
            tasks.append(self.add_fms())
            tasks.append(self.add_nvram())
            tasks.append(self.add_model_text())
        else:
            tasks.append(self.add_cards())
            tasks.append(self.add_mezz())

        # run all the tasks concurrently
        await asyncio.gather(*tasks)
        return self.tmp_img

    async def get_base_img(self, key):
        self.tmp_img = await RackImage(key).get_image()
        self.start_img_event.set()

    async def add_nvram(self):
        if self.config['generation'] == 'x':
            nv1 = (1263, 28)
            nv2 = (1813, 28)
        else:
            nv1 = (1255, 20)
            nv2 = (1805, 20)
            
        if self.config["model_num"] < 70:
            return

        nvram_img = await RackImage("png/pure_fa_x_nvram.png").get_image()
        await self.start_img_event.wait()
        self.tmp_img.paste(nvram_img, nv1)
        self.tmp_img.paste(nvram_img, nv2)

    async def add_cards(self):
        pci = self.config["pci_config"]

        tasks = []
        for x in range(4):
            if pci[x]:
                tasks.append(self.add_card(x, pci[x]))
        await asyncio.gather(*tasks)

    async def add_card(self, slot, card_type):
        y_offset = 378 #y offset from CT1 -> CT0
        if self.config['generation'] == 'x':
            pci_loc = [(1198, 87), (1198, 203), (2069, 87), (2069, 203)]
        elif self.config['generation'] == 'm':
            pci_loc = [(1317, 87), (1317, 201), (2182, 87), (2182, 201)]
        
        if slot < 2:
            height = "fh"
        else:
            height = "hh"
        
        key = "png/pure_fa_{}_{}.png".format(card_type, height)
        card_img = await RackImage(key).get_image()
        await self.start_img_event.wait()
        cord = pci_loc[slot]
        self.tmp_img.paste(card_img, cord)
        ct0_cord = (cord[0], cord[1] + y_offset)
        self.tmp_img.paste(card_img, ct0_cord)

    async def add_mezz(self):
        if self.config["generation"] != "x" or self.config['mezz'] is None:
            return
        key = "png/pure_fa_x_{}.png".format(self.config["mezz"])
        mezz_img = await RackImage(key).get_image()
        await self.start_img_event.wait()
        self.tmp_img.paste(mezz_img, (585, 45))
        self.tmp_img.paste(mezz_img, (585, 425))

    async def add_fms(self):
        curr_module = 0
        for dp in self.config["chassis_datapacks"]:
            fm_str = dp[0]
            fm_type = dp[1]
            num_modules = dp[2]
            dp_size = dp[3]
            dp_start = curr_module

            fm_img = await RackImage("png/pure_fa_dfm.png").get_image()

            if self.config['fm_label']:
                apply_fm_label(fm_img, fm_str, fm_type)

            await self.start_img_event.wait()
            fm_loc = get_chassis_fm_loc(self.config['generation'])
            for x in range(curr_module, min(20, curr_module + num_modules)):
                # self.tmp_img.save("tmp.png")
                self.tmp_img.paste(fm_img, fm_loc[x])
            curr_module += num_modules

            if self.config['dp_label']:
                y_offset = 244
                x_offset = 130
                right = False
                if dp_start > 9:
                    right = True
                self.tmp_img = apply_dp_label(self.tmp_img, dp_size, x_offset, y_offset, right)
    
    async def add_model_text(self):
        if self.config['generation'] == 'x':
            loc = (2759,83)
        else:
            loc = (2745,120)

        await self.start_img_event.wait()
        c = self.config
        draw = ImageDraw.Draw(self.tmp_img)
        font = ImageFont.truetype("Lato-Regular.ttf",size=24)
        text = "{}{}r{}".format(c['generation'].upper(),c['model_num'],c['release'])
        draw.text(loc, text, (255, 255, 255, 220), font=font)


def apply_fm_label(fm_img, fm_str, fm_type):
    draw = ImageDraw.Draw(fm_img)
    font = ImageFont.truetype("Lato-Regular.ttf",size=15)
    w, _ = draw.textsize(fm_str, font=font)
    x_loc = fm_img.size[0] // 2 - w // 2
    draw.text((x_loc,18), fm_str, fill=(199,89,40),font=font)
    w, _ = draw.textsize(fm_type, font=font)
    x_loc = fm_img.size[0] // 2 - w // 2
    draw.text( (34,32), fm_type, fill=(199,89,40),font=font)

def apply_dp_label(img, dp_size, x_offset, y_offset, right):
    #temp image same size as our chassis.
    tmp = Image.new('RGBA', img.size, (0,0,0,0))

    # Create a drawing context for it.
    draw = ImageDraw.Draw(tmp)

    x_buffer = 75
    y_buffer = 60


    box_loc = (x_offset + x_buffer, y_offset + y_buffer)
    if right:
        box_loc = (tmp.size[0] // 2 + x_buffer, y_offset + y_buffer)
    
    
    box_size = (tmp.size[0]  // 2 - 2 * x_buffer - x_offset, 
                (tmp.size[1]  -  2 * y_buffer - y_offset))

    #put DP on left or right
    
    box_loc2 = (box_loc[0]+box_size[0], box_loc[1]+box_size[1])

    
    draw.rectangle((box_loc, box_loc2),fill=(199,89,40,127))
    box_center = ((box_loc[0] + box_loc2[0]) // 2, (box_loc[1] + box_loc2[1]) //2)
    font = ImageFont.truetype("Lato-Regular.ttf",size=85)
    w, h = draw.textsize(dp_size+"TB",font=font)
    text_loc = (box_center[0] - w/2, box_center[1] - h/2)
    draw.text(text_loc, dp_size + "TB", fill=(255, 255, 255, 220),font=font)

    alpha_tmp = img.convert("RGBA")
    return Image.alpha_composite(alpha_tmp, tmp)


# x,y coordinates for all chassis fms.
def get_chassis_fm_loc(model = 'x'):
    ch0_fm_loc = [None] * 28
    ch0_fm_loc[0] = (165, 250)
    ch0_fm_loc[4] = (714, 250)
    ch0_fm_loc[8] = (1265, 250)
    ch0_fm_loc[12] = (1817, 250)
    ch0_fm_loc[20] = (164, 27)

    x_offset = 105
    for x in range(20):
        if ch0_fm_loc[x] is None:
            loc = list(ch0_fm_loc[x - 1])
            loc[0] += int(x_offset)
            ch0_fm_loc[x] = tuple(loc)

    y_offset = x_offset
    x_offset = 550
    for x in range(20, 28):
        if ch0_fm_loc[x] is None:
            loc = list(ch0_fm_loc[x - 1])
            if x % 2 == 0:
                loc[0] += x_offset
                loc[1] -= y_offset
            else:
                loc[1] += y_offset
            ch0_fm_loc[x] = tuple(loc)
    
    if model != 'x':
        #adjust slightly for the //m image
        for x in range(len(ch0_fm_loc)):
            ch0_fm_loc[x] = (ch0_fm_loc[x][0] - 9, ch0_fm_loc[x][1] - 7)
    
    return ch0_fm_loc


# x,y coordinates for all sas fms
def get_sas_fm_loc():
    fm_loc = [None] * 24
    fm_loc[0] = (138, 1)
    fm_loc[12] = (1450, 1)
    x_offset = 105

    for x in range(24):
        if fm_loc[x] is None:
            loc = list(fm_loc[x - 1])
            loc[0] += x_offset
            fm_loc[x] = tuple(loc)
    return fm_loc


class FADiagram():
    def _init_pci_cards(self, config, params):
        # default card config:
        pci_config_lookup = {
            "fa-x10r2-fc": [None, None, "2fc", None],
            "fa-x20r2-fc": [None, None, "2fc", None],
            "fa-x50r2-fc": ["4fc", None, None, None],
            "fa-x70r2-fc": ["4fc", None, "2fc", None],
            "fa-x90r2-fc": ["4fc", None, "2fc", None],
            "fa-x10r2-eth": [None, None, None, None],
            "fa-x20r2-eth": [None, None, None, None],
            "fa-x50r2-eth": ["2eth", None, None, None],
            "fa-x70r2-eth": ["2eth", None, None, None],
            "fa-x90r2-eth": ["2eth", None, None, None],
            "fa-m10r2-fc": [None, None, "2fc", None],
            "fa-m20r2-fc": [None, None, "2fc", None],
            "fa-m50r2-fc": ["2fc", None, "2fc", None],
            "fa-m70r2-fc": ["4fc", None, "2fc", None],
            "fa-m10r2-eth": [None, None, None, None],
            "fa-m20r2-eth": [None, None, "2eth", None],
            "fa-m50r2-eth": ["2eth", None, "2eth", None],
            "fa-m70r2-eth": ["2eth", None, "2eth", None],
            "fa-x70r1-eth": ["2eth", None, "2eth", None],
            "fa-x70r1-fc": ["4fc", None, "2fc", None],
        }

        pci_config = [None, None, None, None]
        pci_lookup_str = "fa-{}{}r{}-{}".format(
            config["generation"],
            config["model_num"],
            config["release"],
            config["protocol"],
        )
        if pci_lookup_str in pci_config_lookup:
            pci_config = pci_config_lookup[pci_lookup_str]

        # add on cards
        if "addoncards" in params:
            for card in params["addoncards"].split(","):

                order = [0, 1]
                if card == "2fc" or card == "2eth":
                    order = [2, 0, 1, 3]

                for slot in order:
                    if pci_config[slot] is None:
                        pci_config[slot] = card
                        break

        config["pci_config"] = pci_config

        # pci3 = '2fc'  overrides everything
        for x in range(4):
            slot = "pci.{}".format(x)
            #try to pull slot specic rules, to override, provide self as default
            pci_config[x] = params.get(slot, pci_config[x])
        

    def _init_datapacks(self, config, params):
       ######################################
        #  Parse Data Packs & Shelf config

        chassis_dp_size_lookup = {
            "4.8": ["480GB", "sas", 10, "4.8"],
            "5": ["500GB", "sas", 10, "5"],
            "9.6": ["960GB", "sas", 10, "9.6"],
            "10": ["1TB", "sas", 10, "10"],
            "19.2": ["1.9TB", "sas", 10, "19.2"],
            "20": ["2TB", "sas", 10, "20"],
            "38": ["3.8TB", "sas", 10, "38"],
            "76": ["7.6TB", "sas", 10, "76"],
            "22": ["2.2TB", "nvme", 10, "22"],
            "45": ["4.5TB", "nvme", 10, "45"],
            "91": ["9.1TB", "nvme", 10, "91"],
            "183": ["18.3TB", "nvme", 10, "183"],
            "127": ["9.1TB", "nvme", 16, "127"],
            "275": ["18.3TB", "nvme", 15, "275"],
        }

        shelf_dp_size_lookup = {
            "11": ["960GB", "sas", 12, "11"],
            "22": ["1.9TB", "sas", 12, "22"],
            "45": ["3.8TB", "sas", 12, "45"],
            "90": ["7.6TB", "sas", 12, "90"],
            "31": ["2.2TB", "nvme", 14, "31"],
            "63": ["4.5TB", "nvme", 14, "63"],
            "127": ["9.1TB", "nvme", 14, "127"],
            "256": ["18.3TB", "nvme", 14, "256"],
        }

        shelves = []
        config["chassis_datapacks"] = []

        if "datapacks" in params:
            if "m" in config["generation"]:
                default_shelf_type = "sas"
            else:
                default_shelf_type = "nvme"
            
            # Of form "38/38-90/0"
            dp_configs = params["datapacks"].split("-")
            chassis = dp_configs[0]

            # Pull shelf info into seperate array
            shelf_configs = []
            if len(dp_configs) > 1:
                shelf_configs = dp_configs[1:]

            # Chassis data pack config:
            datapacks = []

            #not displaying chassis DP if there is a bezel
            if not config["bezel"]:
                for dp in chassis.split("/"):
                    if dp in chassis_dp_size_lookup:
                        datapacks.append(chassis_dp_size_lookup[dp])
                    elif dp == '0':
                        pass
                    else:
                        raise Exception("Unknown Chassis: DP: {}\nPick from One of the Following\n{}".
                                        format(dp,chassis_dp_size_lookup))

                config["chassis_datapacks"] = datapacks

            # Shelf DPs
            for shelf in shelf_configs:
                datapacks = []
                shelf_type = default_shelf_type

                
                for dp in shelf.split("/"):
                    if dp in shelf_dp_size_lookup:
                        datapacks.append(shelf_dp_size_lookup[dp])
                        shelf_type = shelf_dp_size_lookup[dp][1]
                    elif dp == '0':
                        pass
                    else:
                        raise Exception("Unknown Shelf: DP: {}\nPick from One of the Following\n{}".
                                    format(dp,shelf_dp_size_lookup))


                if config['face'] == 'front':
                    shelves.append(
                        {
                            "shelf_type": shelf_type,
                            "datapacks": datapacks,
                            "face": config["face"],
                            "dp_label": config["dp_label"],
                            "fm_label": config["fm_label"],
                        }
                    )
                else:
                    shelves.append({'shelf_type': shelf_type, "face": 'back'})

        config["shelves"] = shelves


    @timeit
    def __init__(self, params):
        # front or back
        config = {}
        
        #mode-str of type  "fa-m70r2" or "fa-x70"
        config["model_str"] = params["model"].lower()
        config["generation"] = config["model_str"][3:4]
        config["model_num"] = int(config["model_str"][4:6])
        config["direction"] = params.get("direction", "up").lower()
        
        if "r" in config["model_str"] and len(config["model_str"]) > 7:
            config["release"] = int(config["model_str"][7:8])
        else:
            config["release"] = 1

        face = params.get("face", "front")
        if face != "front" and face != "back":
            face = "front"
        config["face"] = face
        
        if face == "back":
            config['bezel'] = False
            config["protocol"] = params.get("protocol", "fc").lower()

            default_mezz = 'smezz'
            if config["model_num"] > 20:
                default_mezz = 'emezz'
            config["mezz"] = params.get("mezz", default_mezz )
            self._init_pci_cards(config, params)

        else:
            #face == 'front'
            config["fm_label"] = "fm_label" in params
            config["dp_label"] = "dp_label" in params
            config["bezel"] = params.get("bezel",False)
            #check for string versions of no/false
            if config["bezel"] in ['False','false','no','0']:
                config['bezel'] = False
            
        #need for both as shelf type is encoded in DP sizes
        self._init_datapacks(config, params)
        self.config = config

    @timeit
    async def get_image(self):
        tasks = []

        tasks.append(FAChassis(self.config).get_image())
        for shelf in self.config["shelves"]:
            tasks.append(FAShelf(shelf).get_image())

        # go get the cached versions or build images
        # this returns the results of the all the tasks in a list
        all_images = await asyncio.gather(*tasks)

        if self.config["direction"] == "up":
            all_images.reverse()

        return combine_images_vertically(all_images)


def combine_images_vertically(images):
    widths, heights = zip(*(i.size for i in images))

    total_height = sum(heights)
    total_width = max(widths)

    new_im = Image.new("RGB", (total_width, total_height))

    y_offset = 0
    for im in images:
        # center the x difference
        x_offset = int((total_width - im.size[0]) / 2)
        new_im.paste(im, (x_offset, y_offset))
        y_offset += im.size[1]

    return new_im


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


def init_diagram(params):

    model = params.get('model','fa-x20r2').lower()
    params['model'] = model

    if model.startswith("fa"):
        diagram = FADiagram(params)
    elif model.startswith("fb"):
        diagram = FBDiagram(params)
    else:
        raise Exception("Error unknown model, looking for fa or fb")

    return diagram

@timeit
def generate_id(config):
    config_str = "{}{}".format(version, config)
    return hashlib.sha1(config_str.encode("utf-8")).hexdigest()[:20]

@timeit
def build_img(q, loop, diagram):
    #time.sleep(3)
    try:
        img = loop.run_until_complete(diagram.get_image())
        q.put({'name':'build','img':img})
    except RuntimeError:
        logging.info('CancelledError')
 
    return

@timeit
def check_cache_and_upload(q, key, img_queue):
    #time.sleep(2)
    response = s3.list_objects_v2(
        Bucket=bucket,
        Prefix=key
    )
    for obj in response.get('Contents', []):
        if obj['Key'] == key:
            #we found our cache object
            logger.info("We found a cache object!! Yeah")
            q.put({'name':'cache','result':True})
            return

    #Only if we didn't find it in cache we will upload it.
    img = img_queue.get()
    upload_img(img, key)

@timeit
def upload_img(img, key):
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    s3.put_object(Bucket=bucket, Key=key, Body=buffer, ContentType='image/png')

@timeit
def handler(event, context):
    startTime = datetime.now()
    try:
        if ("queryStringParameters" not in event
        or event["queryStringParameters"] is None):

            return {"statusCode": 200, "body": "no query params. event={} and context={}".format(event,vars(context))}
        
        params = event["queryStringParameters"]

        diagram = init_diagram(params)
        cache_key = "cache/{}".format(generate_id(diagram.config))
        
        result_queue = queue.Queue()
        img_queue = queue.Queue()

        loop = asyncio.get_event_loop()
        check_cache_and_upload_thread = Thread(target=check_cache_and_upload, 
                                               args=(result_queue, cache_key, img_queue))
        build_img_thread = Thread(target=build_img, args=(result_queue, loop, diagram))
        check_cache_and_upload_thread.start()
        build_img_thread.start()
        
        #block until either the image is found in s3 cache, or we built the image locally
        first_result = result_queue.get()
        if first_result['name'] == 'cache':
            #stop trying to build our image
            for task in asyncio.Task.all_tasks():
                task.cancel()
                #logging.info('bye, exiting in a minute...')  
            loop.stop()
            return {
                "statusCode": 301,
                "body": "",
                "headers": {"Location": "{}{}/{}".format(s3_base_url,bucket,cache_key) } 
            }

        #we built the image
        elif first_result['name'] == 'build':
            img = first_result['img']
            img_queue.put(img)
            

            buffered = BytesIO()
            img.save(buffered, format="PNG")
            #buffer_cache_copy = buffered.copy()
        
            #convert to base64 and encode utf-8
            img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            return_data = {
                    "statusCode": 200,
                    "body": img_str,
                    "headers": {"Content-Type": "image/png"},
                    "isBase64Encoded": True
                }
            if 'test' in event:
                img.save('tmp.png')
                #don't want to dump actual image to console.
                del return_data['body']
                return return_data
            else:
                return return_data

            #does uplaod ? thread complete ? 
    
    except Exception as e:
        logger.error("{}\nOriginal Params: {}".format(e, params))
        return {
            "statusCode": 500,
            "body": "{}".format(e),
            "headers": {"Content-Type": "text/plain"}
        }
    finally:
        logger.info(print("time elapsed: {}".format(datetime.now() - startTime)))

    
