import os

# update custom_nodes: git pull
def update_custom_nodes():
    custom_nodes_path = os.path.join(os.path.dirname(__file__), 'custom_nodes')
    os.chdir(custom_nodes_path)
    for node in os.listdir(custom_nodes_path):
        print(f"update {node}")
        if os.path.isdir(node):
            os.chdir(node)
            if os.path.exists('requirements.txt'):
                os.system('git pull')
                os.system('pip install -r requirements.txt')
            else:
                os.system('git pull')
            os.chdir(custom_nodes_path)

if __name__ == '__main__':
    update_custom_nodes()
    