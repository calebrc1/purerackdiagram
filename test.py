import PureRackdiagram
from pprint import pprint
import logging

logger = logging.getLogger()
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
logger.addHandler(ch)


def main():

    event = {
        "queryStringParameters": {
            "model": "fa-m70r2",
            "datapacks": "38/38-45/45-45/45-63/31",
            "chassis": 2,
            "addoncards":"4fc,4fc,2eth",
            "face":"front",
            "fm_label":True,
            "dp_label":True
        },
        "test": True,
    }

    pprint(PureRackdiagram.handler(event, None))


if __name__ == "__main__":
    main()
