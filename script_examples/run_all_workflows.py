#This is an example that uses the websockets api to know when a prompt execution is done
#Once the prompt execution is done it downloads the images using the /history endpoint

import websocket #NOTE: websocket-client (https://github.com/websocket-client/websocket-client)
import uuid
import json
import urllib.request
import urllib.parse
import os
import time

server_address = "127.0.0.1:8188"
client_id = str(uuid.uuid4())

def queue_prompt(prompt):
    p = {"prompt": prompt, "client_id": client_id}
    data = json.dumps(p).encode('utf-8')
    req =  urllib.request.Request("http://{}/prompt".format(server_address), data=data)
    return json.loads(urllib.request.urlopen(req).read())

def get_image(filename, subfolder, folder_type):
    data = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    url_values = urllib.parse.urlencode(data)
    with urllib.request.urlopen("http://{}/view?{}".format(server_address, url_values)) as response:
        return response.read()

def get_history(prompt_id):
    with urllib.request.urlopen("http://{}/history/{}".format(server_address, prompt_id)) as response:
        return json.loads(response.read())

def run_api(ws, file):
    with open(file, "r") as f:
        try:
          prompt = json.load(f)
          prompt_id = queue_prompt(prompt)['prompt_id']
          output_images = {}

          start = time.time()
          while True:
              out = ws.recv()
              if isinstance(out, str):
                  message = json.loads(out)
                  print("recv ", message)
                  if message['type'] == 'executing':
                      data = message['data']
                      if data['node'] is None and data['prompt_id'] == prompt_id:
                          end = time.time()
                          print(f"websocket_api|{file}|{round(end-start, 2)}|Success|{prompt_id}")
                          break #Execution is done
              else:
                  continue #previews are binary data

          history = get_history(prompt_id)[prompt_id]
          for o in history['outputs']:
              for node_id in history['outputs']:
                  node_output = history['outputs'][node_id]
                  if 'images' in node_output:
                      images_output = []
                      for image in node_output['images']:
                          image_data = get_image(image['filename'], image['subfolder'], image['type'])
                          images_output.append(image_data)
                  output_images[node_id] = images_output

          return output_images
        except Exception as e:
          end = time.time()
          # 保留2为小数
          print(f"websocket_api|{file}|{round((end-start), 2)}|Error|{e}")

def run_all_api(ws, path):
  # loop dir to run all api file
  print(f"Run all api.json in {path}")
  for file in os.listdir(path):
    # check is file
    if os.path.isfile(os.path.join(path, file)):
      if file.endswith("_api.json"):
        run_api(ws, os.path.join(path, file))
      else:
        print(f"Skip {file}")
    
    # chedck is dir
    elif os.path.isdir(os.path.join(path, file)):
      run_all_api(ws, os.path.join(path, file))

if __name__ == "__main__":
  ws = websocket.WebSocket()
  ws.connect("ws://{}/ws?clientId={}".format(server_address, client_id))
  # file_path = "./default_api.json"
  # run_api(ws, file_path)

  file_path = "./"
  run_all_api(ws, file_path)


