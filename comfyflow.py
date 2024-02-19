import os

def upload_s3(app_id, file):
    ### upload to s3
    # file: str
    # return: str
    filename = file.split('/')[-1]
    format = filename.split('.')[-1]
    if len(app_id)==0:
        app_id = "default"

    aws_s3_bucket = os.environ.get('AWS_S3_BUCKET')
    aws_region_name = os.environ.get('AWS_REGION_NAME')
    aws_access_key_id = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.environ.get('AWS_SECRET_ACCESS_KEY')

    if not aws_s3_bucket or not aws_region_name or not aws_access_key_id or not aws_secret_access_key:
        print("AWS S3 credentials not found, don't upload to S3")
        return

    try:

        # upload to s3
        from boto3 import client
        s3 = client('s3', aws_access_key_id=aws_access_key_id,
                            aws_secret_access_key=aws_secret_access_key,
                            region_name=aws_region_name)
        key = f"comfyflowapp/{app_id}/{filename}"
        if format in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
            # image
            content_type = f"image/{format}"
        elif format in ['mp4', 'mov', 'webm']:
            # video
            content_type = f"video/{format}"
        else:
            # binary/octet-stream
            content_type = "binary/octet-stream"
        s3.upload_file(file, 'promptart-images', key, ExtraArgs={'ContentType': content_type})
        # s3 url
        url = f"https://{aws_s3_bucket}.s3.{aws_region_name}.amazonaws.com/{key}"
        return url
    except Exception as e:
        print("upload to s3 error", e)
        return

def download_s3(key, filepath):
    ### download from s3
    # key: str
    # subfolder: str
    # filename: str
    # return: str
    aws_s3_bucket = os.environ.get('AWS_S3_BUCKET')
    aws_region_name = os.environ.get('AWS_REGION_NAME')
    aws_access_key_id = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.environ.get('AWS_SECRET_ACCESS_KEY')

    if not aws_s3_bucket or not aws_region_name or not aws_access_key_id or not aws_secret_access_key:
        print("AWS S3 credentials not found, don't upload to S3")
        return

    try:
        print("download from s3", key)
        # download from s3
        import boto3
        s3 = boto3.client('s3', aws_access_key_id=aws_access_key_id,
                            aws_secret_access_key=aws_secret_access_key,
                            region_name=aws_region_name)
        data = s3.get_object(Bucket=aws_s3_bucket, Key=key)['Body'].read()
        with open(filepath, 'wb') as f:
            f.write(data)
        return filepath
    except Exception as e:
        print("download from s3 error", e)
        returnls