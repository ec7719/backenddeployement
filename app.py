from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import os
import boto3
import dotenv
from datetime import datetime
from pathlib import Path
from io import BytesIO

# Load environment variables
dotenv.load_dotenv()

app = Flask(__name__)

# Configure AWS
aws_region = os.getenv('AWS_REGION', 'us-east-1')

# Initialize AWS services
s3 = boto3.client('s3', region_name=aws_region)
rekognition = boto3.client('rekognition', region_name=aws_region)
dynamodb = boto3.resource('dynamodb', region_name=aws_region).Table('Tableaws')

# Constants
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME', 'shailendrasirclasses')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB

# Configure CORS

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', 'https://master.dl76w5z8gkkdv.amplifyapp.com')  # Removed trailing slash
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    
    # Handle OPTIONS method
    if request.method == 'OPTIONS':
        response.headers.add('Access-Control-Max-Age', '1728000')
        response.headers['Content-Type'] = 'text/plain'
        response.headers['Content-Length'] = '0'
        return response

    return response
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def ensure_s3_folder_exists(bucket, folder_path):
    try:
        s3.list_objects_v2(Bucket=bucket, Prefix=folder_path)
        s3.put_object(
            Bucket=bucket,
            Key=f"{folder_path}dummy.txt",
            Body='This is a placeholder file to ensure the folder exists.'
        )
    except Exception as e:
        print(f"Error ensuring S3 folder exists: {e}")
        raise

@app.route('/classes', methods=['GET'])
def get_classes():
    try:
        # List all folders under 'classes/' prefix
        response = s3.list_objects_v2(
            Bucket=S3_BUCKET_NAME,
            Prefix='classes/',
            Delimiter='/'
        )
        
        # Extract class names from common prefixes
        classes = [
            prefix.split('/')[-2] if prefix.endswith('/') else prefix.split('/')[-1]
            for prefix in response.get('CommonPrefixes', [])
        ] if response.get('CommonPrefixes') else []
        
        return jsonify({
            'success': True,
            'classes': classes
        })
    except Exception as e:
        print(f"Error fetching classes: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 400


def upload_to_s3(file_data, class_name, student_name):
    try:
        filename = "image.jpg"  # Generic filename since we're using BytesIO
        key = f"classes/{class_name}/{student_name}/{datetime.now().timestamp()}_{filename}"
        s3.upload_fileobj(
            file_data,
            S3_BUCKET_NAME,
            key,
            ExtraArgs={'ContentType': 'image/jpeg'}
        )
        return key
    except Exception as e:
        print(f"Error uploading to S3: {e}")
        raise

def record_attendance_in_dynamodb(class_name, student_name, status):
    try:
        dynamodb.put_item(Item={
            'awstable': f"{class_name}-{student_name}",
            'className': class_name,
            'studentName': student_name,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'timestamp': datetime.now().isoformat(),
            'status': status
        })
    except Exception as e:
        print(f"Error recording attendance in DynamoDB: {e}")
        raise

def update_attendance_in_dynamodb(class_name, student_name, status):
    try:
        dynamodb.update_item(
            Key={'awstable': f"{class_name}-{student_name}"},
            UpdateExpression="set #status = :status, #timestamp = :timestamp",
            ExpressionAttributeNames={
                "#status": "status",
                "#timestamp": "timestamp"
            },
            ExpressionAttributeValues={
                ":status": status,
                ":timestamp": datetime.now().isoformat()
            }
        )
    except Exception as e:
        print(f"Error updating attendance in DynamoDB: {e}")
        raise

def compare_faces(source_image, target_image_key):
    try:
        response = rekognition.compare_faces(
            SourceImage={'Bytes': source_image.read()},
            TargetImage={'S3Object': {'Bucket': S3_BUCKET_NAME, 'Name': target_image_key}},
            SimilarityThreshold=90
        )
        return len(response.get('FaceMatches', [])) > 0
    except Exception as e:
        print(f"Error comparing faces: {e}")
        raise

@app.route('/hi', methods=['GET'])
def hello():
    return 'Hello World'

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'Welcome to the Flask App! This is the default page.'})

@app.route('/upload', methods=['POST'])
def upload():
    try:
        if 'image' not in request.files:
            return jsonify({'success': False, 'message': 'No file uploaded'}), 400

        file = request.files['image']
        if not file or not allowed_file(file.filename):
            return jsonify({'success': False, 'message': 'Invalid file type'}), 400

        class_name = request.form.get('class')
        student_name = request.form.get('name')
        folder = request.form.get('folder')

        if not all([class_name, student_name, folder]):
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400

        # Read file once and store in memory
        file_data = BytesIO(file.read())

        if folder == 'existing':
            # Reset file pointer and upload
            file_data.seek(0)
            s3_key = upload_to_s3(file_data, class_name, student_name)
            record_attendance_in_dynamodb(class_name, student_name, 'Registered')
            return jsonify({
                'success': True,
                'message': f'Face registered for {student_name}'
            })
        else:
            response = s3.list_objects_v2(
                Bucket=S3_BUCKET_NAME,
                Prefix=f'classes/{class_name}/'
            )

            recognized_name = None

            for item in response.get('Contents', []):
                if any(item['Key'].endswith(ext) for ext in ALLOWED_EXTENSIONS):
                    # Reset file pointer for each comparison
                    file_data.seek(0)
                    if compare_faces(file_data, item['Key']):
                        recognized_name = item['Key'].split('/')[2]
                        break

            if not recognized_name:
                return jsonify({
                    'success': False,
                    'message': 'Face recognition is unmatched among the students'
                }), 400

            status = 'Present' if folder == 'attendance' else 'Checked Out'

            if status == 'Checked Out':
                response = dynamodb.get_item(
                    Key={'awstable': f"{class_name}-{recognized_name}"}
                )
                if 'Item' not in response or response['Item'].get('status') != 'Present':
                    return jsonify({
                        'success': False,
                        'message': f'{recognized_name} cannot check out without being marked present first'
                    }), 400

            update_attendance_in_dynamodb(class_name, recognized_name, status)

            return jsonify({
                'success': True,
                'message': f'{status} marked for {recognized_name}'
            })

    except Exception as e:
        print(f"Error processing upload: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
