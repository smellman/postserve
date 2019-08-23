import tornado.ioloop
import tornado.web
from tornado import httputil
import io
import os

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy import create_engine
from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

import mercantile
import pyproj
import yaml
import sys
import itertools

def GetTM2Source(file):
    with open(file,'r') as stream:
        tm2source = yaml.load(stream)
    return tm2source

def GenerateBaseQuery(layers):
    queries = []
    query = ""
    for layer in layers['Layer']:
        buffer_size = str(layer['properties']['buffer-size'])
        layer_query = layer['Datasource']['table'].lstrip().rstrip()	# Remove lead and trailing whitespace
        layer_query = layer_query[1:len(layer_query)-6]			# Remove enough characters to remove first and last () and "AS t"
        layer_query = layer_query.replace("geometry", f"ST_AsMVTGeom(geometry,!bbox!,4096,{buffer_size},true) AS mvtgeometry")
        base_query = f"SELECT ST_ASMVT(tile, '{layer['id']}', 4096, 'mvtgeometry') FROM ({layer_query} WHERE ST_AsMVTGeom(geometry, !bbox!,4096,{buffer_size},true) IS NOT NULL) AS tile"
        queries.append(base_query)
    query = query + " UNION ALL ".join(queries) + ";"
    return(query)

layers = GetTM2Source("/mapping/data.yml")
base_query = GenerateBaseQuery(layers)
engine = create_engine('postgresql://'+os.getenv('POSTGRES_USER','openmaptiles')+':'+os.getenv('POSTGRES_PASSWORD','openmaptiles')+'@'+os.getenv('POSTGRES_HOST','postgres')+':'+os.getenv('POSTGRES_PORT','5432')+'/'+os.getenv('POSTGRES_DB','openmaptiles'))
inspector = inspect(engine)
DBSession = sessionmaker(bind=engine)
session = DBSession()
#session.execute(prepared)

def bounds(zoom,x,y):
    inProj = pyproj.Proj(init='epsg:4326')
    outProj = pyproj.Proj(init='epsg:3857')
    lnglatbbox = mercantile.bounds(x,y,zoom)
    ws = (pyproj.transform(inProj,outProj,lnglatbbox[0],lnglatbbox[1]))
    en = (pyproj.transform(inProj,outProj,lnglatbbox[2],lnglatbbox[3]))
    return {'w':ws[0],'s':ws[1],'e':en[0],'n':en[1]}

def zoom_to_scale_denom(zoom):						# For !scale_denominator!
    # From https://github.com/openstreetmap/mapnik-stylesheets/blob/master/zoom-to-scale.txt
    map_width_in_metres = 40075016.68557849
    tile_width_in_pixels = 256.0
    standardized_pixel_size = 0.00028
    map_width_in_pixels = tile_width_in_pixels*(2.0**zoom)
    return str(map_width_in_metres/(map_width_in_pixels * standardized_pixel_size))

def replace_tokens(query,s,w,n,e,scale_denom):
    return query.replace("!bbox!","ST_MakeBox2D(ST_Point("+w+", "+s+"), ST_Point("+e+", "+n+"))").replace("!scale_denominator!",scale_denom).replace("!pixel_width!","256").replace("!pixel_height!","256")

def get_mvt(zoom,x,y):
    try:								# Sanitize the inputs
        sani_zoom,sani_x,sani_y = float(zoom),float(x),float(y)
        del zoom,x,y
    except:
        print('suspicious')
        return 1

    scale_denom = zoom_to_scale_denom(sani_zoom)
    tilebounds = bounds(sani_zoom,sani_x,sani_y)
    s,w,n,e = str(tilebounds['s']),str(tilebounds['w']),str(tilebounds['n']),str(tilebounds['e'])
    sent_query = replace_tokens(base_query,s,w,n,e,scale_denom)
    response = list(session.execute(sent_query))
    _layers = filter(None,list(itertools.chain.from_iterable(response)))
    final_tile = b''
    for layer in _layers:
        final_tile = final_tile + io.BytesIO(layer).getvalue()
    return final_tile

class GetTile(tornado.web.RequestHandler):
    def get(self, zoom,x,y):
        self.set_header("Content-Type", "application/x-protobuf")
        self.set_header("Content-Disposition", "attachment")
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Cache-Control", "private")
        response = get_mvt(zoom,x,y)
        self.write(response)

class HealthCheck(tornado.web.RequestHandler):
    def get(self):
        self.write("OK")

def m():
    if __name__ == "__main__":
        # Make this prepared statement from the tm2source
        application = tornado.web.Application(
            [
                (r"/tiles/([0-9]+)/([0-9]+)/([0-9]+).pbf", GetTile),
                ("/healthcheck", HealthCheck)
            ]
        )
        print("Postserve started..")
        application.listen(8080)
        tornado.ioloop.IOLoop.current().start()

m()
