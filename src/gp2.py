%pip install transformers
import numpy as np
import pandas as pd
import random
import torch
# import fire
import logging
import os
import csv
from torch.utils.data import Dataset, DataLoader
from transformers import GPT2Tokenizer, GPT2LMHeadModel, AdamW, get_linear_schedule_with_warmup
from tqdm import tqdm, trange
import torch.nn.functional as F
import google.colab
from google.colab import drive
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(device)
drive.mount('/content/gdrive')

class Books(Dataset):

	def __init__(self, control_code, truncate=False, gpt2_type="gpt2", max_length=768, train_fpaths=None):

		self.tokenizer = GPT2Tokenizer.from_pretrained(gpt2_type)
		self.sentences = []

		for fp in train_fpaths:
			with open(fp) as inp:
				sentences = inp.readlines()
				for sent in sentences:
					encoding = torch.tensor(self.tokenizer.encode(f"<|{control_code}|>{sent[:max_length]}<|endoftext|>"))
					self.sentences.append(encoding)
		if truncate:
			self.sentences = self.sentences[:20000]
		self.sentence_count = len(self.sentences)

	def __len__(self):
		return self.sentence_count

	def __getitem__(self, item):
		return self.sentences[item]


def pack_tensor(new_tensor, packed_tensor, max_seq_len):
	if packed_tensor is None:
		return new_tensor, True, None
	if new_tensor.size()[1] + packed_tensor.size()[1] > max_seq_len:
		return packed_tensor, False, new_tensor
	else:
		packed_tensor = torch.cat([new_tensor, packed_tensor[:, 1:]], dim=1)
		return packed_tensor, True, None

def train(
	dataset,
	model,
	tokenizer,
	batch_size=16,
	epochs=4,
	lr=2e-5,
	max_seq_len=400,
	warmup_steps=5000,
	gpt2_type="gpt2",
	device=device,
	output_dir=".",
	output_prefix="wreckgar",
	test_mode=False,
	save_model_on_epoch=False,
	):

	acc_steps = 100

	model = model.to(device)
	model.train()

	optimizer = AdamW(model.parameters(), lr=lr)
	scheduler = get_linear_schedule_with_warmup(
	    optimizer, num_warmup_steps=warmup_steps, num_training_steps=-1
	)

	train_dataloader = DataLoader(dataset, batch_size=1, shuffle=True)

	accumulating_batch_count = 0
	input_tensor = None

	for epoch in range(epochs):
		print(f"Training epoch {epoch}")
		for idx, entry in tqdm(enumerate(train_dataloader)):
			(input_tensor, carry_on, remainder) = pack_tensor(entry, input_tensor, 768)

			if carry_on and idx != len(train_dataloader) - 1:
				continue

			input_tensor = input_tensor.to(device)
			outputs = model(input_tensor, labels=input_tensor)
			loss = outputs[0]
			loss.backward()
			
			if (accumulating_batch_count % batch_size) == 0:
				optimizer.step()
				scheduler.step()
				optimizer.zero_grad()
				model.zero_grad()

			accumulating_batch_count += 1
			input_tensor = None
		if save_model_on_epoch:
			torch.save(
				model.state_dict(),
				os.path.join(output_dir, f"{output_prefix}-{epoch}.pt"),
			)
		print(os.path.join(output_dir, f"{output_prefix}-{epoch}.pt"))
		quit()
		%cp "/home/output/models/spinoff-" + str(epoch) + ".pt" "gdrive/My Drive/gpt-2.pt"
	
	return model

def generate(
	model,
	tokenizer,
	prompt,
	entry_count=10,
	entry_length=100,
	top_p=0.8,
	temperature=1.,
):

	model.eval()

	generated_num = 0
	generated_list = []

	filter_value = -float("Inf")

	with torch.no_grad():

		for entry_idx in trange(entry_count):

			entry_finished = False

			generated = torch.tensor(tokenizer.encode(prompt)).unsqueeze(0)

			# Using top-p (nucleus sampling): https://github.com/huggingface/transformers/blob/master/examples/run_generation.py

			for i in range(entry_length):
				outputs = model(generated, labels=generated)
				loss, logits = outputs[:2]
				logits = logits[:, -1, :] / (temperature if temperature > 0 else 1.0)

				sorted_logits, sorted_indices = torch.sort(logits, descending=True)
				cumulative_probs = torch.cumsum(F.softmax(sorted_logits, dim=-1), dim=-1)

				sorted_indices_to_remove = cumulative_probs > top_p
				sorted_indices_to_remove[..., 1:] = sorted_indices_to_remove[
				    ..., :-1
				].clone()
				sorted_indices_to_remove[..., 0] = 0

				indices_to_remove = sorted_indices[sorted_indices_to_remove]
				logits[:, indices_to_remove] = filter_value

				next_token = torch.multinomial(F.softmax(logits, dim=-1), num_samples=1)
				generated = torch.cat((generated, next_token), dim=1)

				if next_token in tokenizer.encode("<|endoftext|>"):
					entry_finished = True

				if entry_finished:

					generated_num = generated_num + 1

					output_list = list(generated.squeeze().numpy())
					output_text = tokenizer.decode(output_list)

					generated_list.append(output_text)
					break
	        
			if not entry_finished:
				output_list = list(generated.squeeze().numpy())
				output_text = f"{tokenizer.decode(output_list)}<|endoftext|>" 
				generated_list.append(output_text)
	            
	return generated_list

def compute_perplexity(model, test_fpath):

  tokenizer = GPT2Tokenizer.from_pretrained("gpt2")
  test = load_dataset('wikitext', 'wikitext-2-raw-v1', split='test')

  encodings = tokenizer('\n\n'.join(test['text']), return_tensors='pt')
  
  max_length = model.config.n_positions
  stride = 512

  lls = []
  for i in tqdm(range(0, encodings.input_ids.size(1), stride)):
      begin_loc = max(i + stride - max_length, 0)
      end_loc = min(i + stride, encodings.input_ids.size(1))
      trg_len = end_loc - i    # may be different from stride on last loop
      input_ids = encodings.input_ids[:,begin_loc:end_loc].to(device)
      target_ids = input_ids.clone()
      target_ids[:,:-trg_len] = -100

      with torch.no_grad():
          outputs = model(input_ids, labels=target_ids)
          log_likelihood = outputs[0] * trg_len

      lls.append(log_likelihood)

  ppl = torch.exp(torch.stack(lls).sum() / end_loc)


train_fpaths = ["/home/data/train/hgg_train.txt", "/home/data/train/fish_train.txt", "/home/data/train/restaurant_train.txt", "/home/data/train/timetravel_train.txt", "/home/data/train/worldwar_train.txt", "/home/data/train/universe_train.txt"]
dataset = Books("<|sentence|>", truncate=False, gpt2_type="gpt2", train_fpaths=train_fpaths)
gpt2_type = "gpt2"


model = train(
    dataset,
    GPT2LMHeadModel.from_pretrained(gpt2_type),
    GPT2Tokenizer.from_pretrained(gpt2_type),
    batch_size=16,
    epochs=10,
    lr=3e-5,
    max_seq_len=140,
    warmup_steps=5000,
    gpt2_type=gpt2_type,
    device=device,
    output_dir="/home/output/models/",
    output_prefix="spinoff",
    save_model_on_epoch=True
)

story = generate(model.to("cpu"), GPT2Tokenizer.from_pretrained(gpt2_type),"<|sentence|>",entry_count=100)
for i, sentence in enumerate(story):
	print("Sentence " + str(i) + ": ")
	print(sentence)