import os

# update custom_nodes: git pull
def update_custom_nodes():
    custom_nodes_path = os.path.join(os.path.dirname(__file__), 'custom_nodes')
    os.chdir(custom_nodes_path)
    requirements = {"ultralytics": "none"}
    for node in os.listdir(custom_nodes_path):
        if os.path.isdir(node):
            print(f"update {node}")
            os.chdir(node)
            # git pull
            # os.system('git pull')

            # read requirements.txt
            if os.path.exists('requirements.txt'):
                with open('requirements.txt', 'r') as file:
                    for line in file:
                        if line.startswith('#'):
                            continue
                        elif len(line.strip()) >0 :
                            requirements[line.strip()] = 'none'

            os.chdir(custom_nodes_path)
    
    # write requirements_custom_nodes.txt
    print(os.getcwd())
    print("write requirements_custom_nodes.txt ...\n")
    with open('requirements_custom_nodes.txt', '+w') as file:
        for key in requirements.keys():
            print(f"{key}")
            file.write(f"{key}\n")

if __name__ == '__main__':
    update_custom_nodes()
    
