import requests
import numpy
import time
import math
import configparser
import fiona
import threading
import os
import sys
from shapely.geometry import Point, shape
from PIL import Image
from colour import Color
from gpiozero import Button
from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics
from threading import Timer
from colorgradient import create_multi_color

# Set working directory
os.chdir(sys.path[0])

update_running = False
boot_running = True

try:
    def button_clicked():
        global update_running
        global index

        # If we're already handling a button press, just return
        if update_running == True:
            return

        update_running = True
        # Switch to the next weather parameter
        index = index + 1
        index = index % len(configs)
        update_config()
        create_image(data)
        # Wait a bit before we allow the next button press
        time.sleep(0.5)
        update_running = False

    # Listen to button clicks on GPIO 19. 
    switch_button = Button(19)
    switch_button.when_pressed = button_clicked
except:
    pass

try:
    # Define a cell class
    class Cell:
        def __init__(self, x, y, lat, lng, isLand):
            self.x = x
            self.y = y
            self.lat = lat
            self.lng = lng
            self.isLand = isLand
            self.json = None

    def calculate_grid():
        print ('Calculating grid')
        # Initially, I projected the coordinates to a different projection, did the calculations there,
        # then projected it back. Most of the packages I tried wouldn't work well on the Pi, so I gage up.
        transformed_sw = (sw[1], sw[0])
        transformed_ne = (ne[1], ne[0])

        # Create ranges for each dimension
        xs = numpy.linspace(transformed_sw[0], transformed_ne[0], num = rows + 1)
        ys = numpy.linspace(transformed_sw[1], transformed_ne[1], num = cols + 1)

        # Fill the grid
        data = []
        for y in range(len(ys) - 1):
            yData = []
            cy = ys[y] + abs(ys[y + 1] - ys[y]) / 2
            for x in range(len(xs) - 1):
                cx = xs[x] + abs(xs[x + 1] - xs[x]) / 2

                p = (cy, cx)

                # Create a cell object for each LED
                cell = Cell(x, y, p[0], p[1], False)
                yData.append(cell)

            data.append(yData)
        return data

    def api_call(url, params):
      resp = requests.get(url, params=params)

      counter = 0
      while resp.status_code != 200 and counter < 10:
          counter = counter + 1
          time.sleep(1)
          resp = requests.get(url, params=params)

      return resp.json()

    def get_data(data):
        print ('Getting data')
        x = sw[1]

        # First, let's get all cities in the requested area.
        # The call has to be below 25 square degrees for the free API tier, so just iterate per dimension
        # Keep a mapping of city coordinates to city data
        mapping = {}
        while x < ne[1]:
            y = sw[0]
            while y < ne[0]:

                json = api_call('http://api.openweathermap.org/data/2.5/box/city', {
                    'units': 'metric',
                    'appid': api_key,
                    'bbox': '{:f},{:f},{:f},{:f},10000'.format(x, y, x + 4.9, y + 4.9)
                })

                if ('list' in json):
                    l = json['list']

                    for location in l:
                        if ('coord' in location):
                            coord = location['coord']

                            if ('Lat' in coord and 'Lon' in coord):
                                lat = coord['Lat']
                                lng = coord['Lon']

                                key = '{:f}|{:f}'.format(lat, lng)

                                if key not in mapping:
                                    mapping[key] = location

                # Increase coordinates
                y = y + 4.9
            x = x + 4.9

            # Wrap longitude
            if x >= 180:
                x = x - 360

        # Now we iterate the grid and try to find the closest match for each cell
        for y in range(len(data)):
            for x in range(len(data[y])):
                cell = data[y][x]

                # Only consider land
                if cell.isLand:
                    # Find the closest city
                    minDistance = float('Inf')
                    nn = None

                    # Iterate all cities
                    for key, value in mapping.items():
                        targetLat = value['coord']['Lat']
                        targetLng = value['coord']['Lon']

                        # Haversine formula to get distance in kilometers
                        R = 6372.8
                        dLat = math.radians(targetLat - cell.lat)
                        dLon = math.radians(targetLng - cell.lng)
                        lat1 = math.radians(cell.lat)
                        lat2 = math.radians(targetLat)
                        a = math.sin(dLat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dLon / 2)**2
                        c = 2 * math.asin(math.sqrt(a))
                        distance = R * c

                        # Keep the closest city
                        if distance < minDistance:
                            minDistance = distance
                            nn = value

                    # If nothing was found or the closest city is more than 20 kilometers away, make an individual API call
                    # for the specific lat/lng values
                    if nn == None or minDistance >= 20:
                        nn = api_call('http://api.openweathermap.org/data/2.5/weather', params = {
                            'units': 'metric',
                            'lat': cell.lat,
                            'lon': cell.lng,
                            'appid': api_key
                        })

                    # The API is inconsistent when it comes to the cloud parameter. It's `clouds.all` in the individual calls
                    # and `clouds.today` in the cities box call. Map them all to the same.
                    if nn != None and 'clouds' in nn and 'today' in nn['clouds']:
                        nn['clouds']['all'] = nn['clouds']['today']

                    cell.json = nn
                else:
                    cell.json = None

    def create_image(data):
        print ('Generating image')
        # Clear the offscreen canvas
        offscreen.Clear()
        
        # Calculate minimum and maximum value for the current climate parameter
        min_value = float('inf')
        max_value = float('-inf')

        # The grid representing the LED pixel color
        image_data = []

        # Now let's calculate min and max
        for y in range(len(data)):
            row_data = []
            for x in range(len(data[y])):
                cell = data[y][x]

                # If it's land
                if cell.isLand:
                    value = None

                    # Get the json. Now we iterate the json to find the current climate property.
                    nn = cell.json
                    current = nn
                    index = 0
                    for index in range(len(weather_json_path)):
                        if current != None and weather_json_path[index] in current:
                            current = current[weather_json_path[index]]
                            value = current
                        else:
                            value = None

                    # If there is a value, compare it to min and max
                    if value != None:
                        min_value = min(min_value, value)
                        max_value = max(max_value, value)

                    row_data.append(value)
                else:
                    row_data.append(None)
            image_data.append(row_data)

        mn = min_value
        mx = max_value

        if config_key in min_max:
            mn = min_max[config_key][0]
            mx = min_max[config_key][1]

        # Now that we have those values, calculate the color for each LED
        for y in range(len(data)):
            for x in range(len(data[y])):
                # Get the cell and its value
                cell = data[y][x]
                value = image_data[y][x]

                if value != None:
                    # For the special case where min == max, use the last color
                    if mn == mx:
                        color = colors[len(colors) - 1]
                    else:
                        # Normalize the value into the interval [0, color_count] based on min and max.
                        
                        c_index = math.floor(0 + ((value - mn) * ((color_count - 1) - 0))/(mx - mn))
                        color = colors[c_index]
                    # Set the color
                    offscreen.SetPixel(x, cols - y - 1, round(color.red * 255), round(color.green * 255), round(color.blue * 255))
                else:
                    if cell.isLand:
                        offscreen.SetPixel(x, cols - y - 1, 50, 50, 50)
                    else:
                        offscreen.SetPixel(x, cols - y - 1, 0, 0, 0)

        # Now let's add the min/max display to the top left corner
        if min_value != float('inf'):
            min_color = colors[math.floor(0 + ((min_value - mn) * ((color_count - 1) - 0))/(mx - mn))]
        else:
            min_color = colors[0]
        if max_value != float('-inf'):
            max_color = colors[math.floor(0 + ((max_value - mn) * ((color_count - 1) - 0))/(mx - mn))]
        else:
            max_color = colors[len(colors) - 1]

        # Draw the min rectangle
        offscreen.SetPixel(1, 2, round(min_color.red * 255), round(min_color.green * 255), round(min_color.blue * 255))
        offscreen.SetPixel(1, 3, round(min_color.red * 255), round(min_color.green * 255), round(min_color.blue * 255))
        offscreen.SetPixel(1, 4, round(min_color.red * 255), round(min_color.green * 255), round(min_color.blue * 255))
        offscreen.SetPixel(2, 2, round(min_color.red * 255), round(min_color.green * 255), round(min_color.blue * 255))
        offscreen.SetPixel(2, 3, round(min_color.red * 255), round(min_color.green * 255), round(min_color.blue * 255))
        offscreen.SetPixel(2, 4, round(min_color.red * 255), round(min_color.green * 255), round(min_color.blue * 255))
        # And the text
        if min_value != float('inf'):
            graphics.DrawText(offscreen, font, 4, 6, textColor, str(round(min_value)))
        else:
            graphics.DrawText(offscreen, font, 4, 6, textColor, '-')

        # Draw the max rectangle
        offscreen.SetPixel(1, 9, round(max_color.red * 255), round(max_color.green * 255), round(max_color.blue * 255))
        offscreen.SetPixel(1, 10, round(max_color.red * 255), round(max_color.green * 255), round(max_color.blue * 255))
        offscreen.SetPixel(1, 11, round(max_color.red * 255), round(max_color.green * 255), round(max_color.blue * 255))
        offscreen.SetPixel(2, 9, round(max_color.red * 255), round(max_color.green * 255), round(max_color.blue * 255))
        offscreen.SetPixel(2, 10, round(max_color.red * 255), round(max_color.green * 255), round(max_color.blue * 255))
        offscreen.SetPixel(2, 11, round(max_color.red * 255), round(max_color.green * 255), round(max_color.blue * 255))
        # And the text
        if max_value != float('-inf'):
            graphics.DrawText(offscreen, font, 4, 13, textColor, str(round(max_value)))
        else:
            graphics.DrawText(offscreen, font, 4, 13, textColor, '-')

        # Swap it onto the matrix
        matrix.SwapOnVSync(offscreen)

        # If the delay timer to switch the display off is still running, cancel it
        global timer
        if timer.is_alive():
            timer.cancel()

        # Then start a new timer to clear the display       
        timer = Timer(turn_off_delay, clear_display)
        timer.start()

    def clear_display():
        # Clear the offscreen canvase
        offscreen.Clear()
        # Sync it onto the matrix
        matrix.SwapOnVSync(offscreen)

    def update_config():
        # Get the config
        global colors
        global config_key
        global color_range
        global weather_json_path
        config_key = list(configs.keys())[index]
        color_range = configs[config_key]
        # Get the json path
        weather_json_path = config_key.split('.')

        # Create the color gradient
        colors = create_multi_color(color_range, color_count)

    def show_splash():
        # Load the splash image
        image = Image.open('img/splash.png')
        matrix.SetImage(image.convert('RGB'), 0, 0)

        # Load the spinner
        image = Image.open('img/spinner.gif')

        while True:
            # Iterate the gif frames
            for frame in range(0, image.n_frames):
                # Stop once the boot screen is hidden
                if boot_running == False:
                    return
                
                # Seek the current frame, then display it
                image.seek(frame)
                matrix.SetImage(image.convert('RGB'), 4, 36)
                time.sleep(0.1)

    # Load the configuration file
    config = configparser.ConfigParser()
    config.read_file(open(r'config.txt'))

    # Read these from a config file
    cols = int(config.get('weather-led-matrix', 'cols'))
    rows = int(config.get('weather-led-matrix', 'rows'))
    color_count = int(config.get('weather-led-matrix', 'color_count'))
    api_key = config.get('weather-led-matrix', 'api_key')
    turn_off_delay = int(config.get('weather-led-matrix', 'turn_off_delay'))
    shape_file = config.get('weather-led-matrix', 'shape_file')
    # Create corners of rectangle to be transformed to a grid
    sw = (float(config.get('weather-led-matrix', 'lat_south')), float(config.get('weather-led-matrix', 'lon_west')))
    ne = (float(config.get('weather-led-matrix', 'lat_north')), float(config.get('weather-led-matrix', 'lon_east')))

    # Set up the display off timer
    timer = Timer(turn_off_delay, clear_display)

    # The index of the stat to show, start with the first
    index = 1

    # Dict with all the configs we want to show. Button will cycle through them.
    configs = {
        'rain.1h': [Color('#034ea2'), Color('#303a99'), Color('#5e2390'), Color('#8b0d88'), Color('#c00384'), Color('#f10080')],
        'main.temp': [Color('#3e236e'), Color('#0500ff'), Color('#0032ff'), Color('#00d4ff'), Color('#3eff00'), Color('#FFd200'), Color('#FF6e00'), Color('#FF0a00'), Color('#b90000')],
        'wind.speed': [Color('#fef720'), Color('#9bfa24'), Color('#1bf118'), Color('#31db92'), Color('#27bbe0'), Color('#1c6ff8')],
        'clouds.all': [Color('#323232'), Color('#26C6F8')]
    }

    min_max = {}
    # Parse min max scales from the config file
    try:
        min_max['main.temp'] = [int(config.get('weather-led-matrix', 'min_temp')), int(config.get('weather-led-matrix', 'max_temp'))]
    except:
        pass

    # Initialize the matrix with the parameters
    options = RGBMatrixOptions()
    options.rows = rows
    options.cols = cols
    options.chain_length = 1
    options.parallel = 1
    # We're using the PWM hardware hack to prevent flickering.
    # If not using the hack, use 'adafruit-hat' instead.
    options.hardware_mapping = 'adafruit-hat-pwm'
    # This is required if using PWM. Set to `False` otherwise
    options.disable_hardware_pulsing = True
    # Rotate the display
    options.pixel_mapper_config = 'Rotate:270'
    matrix = RGBMatrix(options = options)

    # Create an offscreen canvas for better rendering
    offscreen = matrix.CreateFrameCanvas()
    # Load the font
    font = graphics.Font()
    font.LoadFont('4x6.bdf')
    # And text color
    textColor = graphics.Color(255, 255, 255)

    # Start the splash screen in a thread
    thread = threading.Thread(target=show_splash)
    thread.start()

    colors = []
    # Update the current configuration
    update_config()

    # Calculate the grid
    data = calculate_grid()

    # Try to load the land-lookup file
    try:
        # If we've already run the land lookup, this file should exist and we can load the configuration from there.
        isLandLookup = numpy.loadtxt('grid-lookup.txt').reshape(cols, rows)
        for y in range(len(data)):
            for x in range(len(data[y])):
                cell = data[y][x]
                cell.isLand = isLandLookup[y][x]
    except:
        print('Parsing shape file')
        isLandLookup = []
        with fiona.open(shape_file) as fiona_collection:
            # In this case, we'll assume the shapefile only has one record/layer (e.g., the shapefile
            # is just for the borders of a single country, etc.).
            shapefile_record = next(iter(fiona_collection))

            # Use Shapely to create the polygon
            shape = shape( shapefile_record['geometry'] )

            for y in range(len(data)):
                rowData = []
                for x in range(len(data[y])):
                    cell = data[y][x]
                    # For each grid cell, check whether it's inside the polygon or not
                    cell.isLand = shape.contains(Point(cell.lng, cell.lat))
                    rowData.append(cell.isLand)
                isLandLookup.append(rowData)

        # Save the lookup result to a file so we can read it again on the next run.
        numpy.savetxt('grid-lookup.txt', isLandLookup, fmt="%2i")

    # Keep track of time
    starttime = time.time()

    # Main event loop
    while (True):
        # Get the weather data
        get_data(data)
        # Disable the boot screen
        boot_running = False
        # Create the image based on the data and display it
        create_image(data)

        # This will make sure the runs start at least 5 minutes apart
        time.sleep(300.0 - ((time.time() - starttime) % 300.0))

except KeyboardInterrupt:
    # Clear the matrix display on interrupt
    offscreen.Clear()
    matrix.SwapOnVSync(offscreen)
