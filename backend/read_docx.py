
import docx
import sys

def read_docx(file_path):
    doc = docx.Document(file_path)
    fullText = []
    for para in doc.paragraphs:
        fullText.append(para.text)
    return '\n'.join(fullText)

if __name__ == "__main__":
    path = r"c:\Users\20231\Desktop\daisj\需求整合.docx"
    try:
        content = read_docx(path)
        print(content)
    except Exception as e:
        print(f"Error: {e}")
